"""The worker agent against a real server: cameras, events with photos, live view, a dropped
connection, the lease, and the outbox that keeps reports until the server has them."""

import asyncio
import base64
import contextlib
import hashlib
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from tests.conftest import FakeDetector, serve
from tests.test_workers import add_worker, local_processing
from yardlens_api.api.deps import Container
from yardlens_api.core.enums import EventType, SessionStatus
from yardlens_api.db.models import Event
from yardlens_api.schemas.worker_protocol import WORKER_MESSAGES, EventReport, unpack
from yardlens_api.services.camera_worker import WorkerEvent, encode_jpeg
from yardlens_api.services.loading import SessionState
from yardlens_api.services.workers import certificate_fingerprint
from yardlens_api.worker.agent import Agent
from yardlens_api.worker.config import WorkerSettings
from yardlens_api.worker.outbox import Outbox

FRAME = encode_jpeg(np.full((120, 160, 3), 90, dtype=np.uint8), 80)


@pytest.fixture
async def camera_url() -> AsyncIterator[str]:
    """An HTTP camera that answers every request with a still picture."""

    async def frame(_: Request) -> Response:
        return Response(FRAME, media_type="image/jpeg")

    async with serve(Starlette(routes=[Route("/frame.jpg", frame)])) as url:
        yield f"{url}/frame.jpg"


@contextlib.asynccontextmanager
async def running_agent(
    server: str, token: str, data_dir: Path, *, lease: float = 60.0
) -> AsyncGenerator[Agent]:
    settings = WorkerSettings(
        server=server,
        worker_token=SecretStr(token),
        data_dir=data_dir,
        low_priority=False,
        hardware_decode=False,
    )
    agent = Agent(settings, detector=FakeDetector(), lease_seconds=lease)
    task = asyncio.create_task(agent.run())
    try:
        await asyncio.wait_for(agent.connected.wait(), 10)
        yield agent
    finally:
        agent.stop()
        await asyncio.wait_for(task, 15)


async def until(condition: Callable[[], bool], seconds: float = 15.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def http_camera(client: httpx.AsyncClient, url: str) -> int:
    response = await client.post(
        "/api/cameras",
        json={"name": "Dock", "source_type": "http", "url": url, "inference_fps": 5},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def saved_event(container: Container, camera_id: int, kind: EventType) -> Event | None:
    async with container.session_factory() as session:
        return await session.scalar(
            select(Event).where(Event.camera_id == camera_id, Event.type == kind.value)
        )


async def test_a_worker_runs_a_camera_for_the_server(
    client: httpx.AsyncClient,
    container: Container,
    live_server: str,
    camera_url: str,
    tmp_path: Path,
) -> None:
    await local_processing(client, enabled=False)
    worker_id, token = await add_worker(client)
    async with running_agent(live_server, token, tmp_path / "worker") as agent:
        camera_id = await http_camera(client, camera_url)
        await until(lambda: camera_id in agent.running())

        entered: Event | None = None
        for _ in range(300):
            entered = await saved_event(container, camera_id, EventType.OBJECT_ENTERED)
            if entered is not None:
                break
            await asyncio.sleep(0.05)
        assert entered is not None, "no event from the worker"
        assert entered.class_name == "car"
        assert entered.snapshot_path is not None
        server_photo = container.settings.snapshots_dir / entered.snapshot_path
        assert server_photo.read_bytes().startswith(b"\xff\xd8")
        # Delivered: the worker keeps no copy.
        worker_photo = tmp_path / "worker" / "snapshots" / entered.snapshot_path
        await until(lambda: not worker_photo.exists())

        camera = (await client.get(f"/api/cameras/{camera_id}")).json()
        assert camera["runtime"]["status"] == "online"
        assert camera["node"]["worker_id"] == worker_id

        # The live view comes through the server.
        for path in ("snapshot.jpg", "snapshot.jpg?raw=true"):
            response = await client.get(f"/api/cameras/{camera_id}/{path}")
            assert response.status_code == 200, path
            assert response.content.startswith(b"\xff\xd8")

        resources = (await client.get("/api/workers")).json()["workers"][0]
        assert resources["online"] is True
        assert resources["info"]["physical_cores"] >= 1


async def test_a_dropped_connection_changes_nothing(
    client: httpx.AsyncClient,
    container: Container,
    live_server: str,
    camera_url: str,
    tmp_path: Path,
) -> None:
    await local_processing(client, enabled=False)
    worker_id, token = await add_worker(client)
    async with running_agent(live_server, token, tmp_path / "worker") as agent:
        camera_id = await http_camera(client, camera_url)
        await until(lambda: camera_id in agent.running())
        epoch = agent.running()[camera_id]
        node = container.cameras.node(worker_id)
        assert node is not None

        node.close(1011, "network trouble")
        await until(lambda: container.cameras.node(worker_id) not in (None, node))
        # Same camera, same epoch: it never stopped.
        assert agent.running() == {camera_id: epoch}
        placement = container.cameras.placement(camera_id)
        assert placement is not None
        assert placement.epoch == epoch
        await until(lambda: container.cameras.runtime(camera_id).status.value == "online")


async def test_a_cut_off_worker_stops_its_cameras_after_the_lease(
    client: httpx.AsyncClient, live_server: str, camera_url: str, tmp_path: Path
) -> None:
    await local_processing(client, enabled=False)
    worker_id, token = await add_worker(client)
    async with running_agent(live_server, token, tmp_path / "worker", lease=0.5) as agent:
        camera_id = await http_camera(client, camera_url)
        await until(lambda: camera_id in agent.running())
        # Removed: the server cuts it off for good and gives its cameras away.
        assert (await client.delete(f"/api/workers/{worker_id}")).status_code == 204
        await until(lambda: not agent.running(), seconds=5.0)


# -- downloads -----------------------------------------------------------------------------------


async def test_models_are_served_to_workers_only(
    client: httpx.AsyncClient, anon_client: httpx.AsyncClient, container: Container
) -> None:
    weights = b"not really a model"
    (container.settings.models_dir / "sacks.pt").write_bytes(weights)
    _, token = await add_worker(client)
    url = "/api/workers/models/sacks.pt"

    assert (await anon_client.get(url)).status_code == 401
    assert (
        await anon_client.get(url, headers={"Authorization": "Bearer ylw_1_x"})
    ).status_code == 401
    response = await anon_client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.content == weights
    assert response.headers["x-content-sha256"] == hashlib.sha256(weights).hexdigest()

    missing = await anon_client.get(
        "/api/workers/models/yolo26s.pt", headers={"Authorization": f"Bearer {token}"}
    )
    assert missing.status_code == 404  # the worker downloads stock models itself
    traversal = await anon_client.get(
        "/api/workers/models/..%2F..%2Fauth-secret", headers={"Authorization": f"Bearer {token}"}
    )
    assert traversal.status_code == 404


async def test_the_certificate_authority_is_public(
    anon_client: httpx.AsyncClient, container: Container, tmp_path: Path
) -> None:
    assert (await anon_client.get("/api/workers/ca.pem")).status_code == 404
    der = os.urandom(300)
    pem = (
        b"-----BEGIN CERTIFICATE-----\n" + base64.encodebytes(der) + b"-----END CERTIFICATE-----\n"
    )
    path = tmp_path / "root.crt"
    path.write_bytes(pem)
    container.settings.ca_cert_path = path
    response = await anon_client.get("/api/workers/ca.pem")
    assert response.status_code == 200
    assert response.content == pem
    assert certificate_fingerprint(response.content) == hashlib.sha256(der).hexdigest()


# -- outbox --------------------------------------------------------------------------------------


def _photo(root: Path, relative: str, data: bytes = FRAME) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return relative


def _files(frame: str | bytes) -> list[bytes]:
    _, files = unpack(WORKER_MESSAGES, frame)
    return files


def test_the_outbox_forgets_what_the_server_has(tmp_path: Path) -> None:
    outbox = Outbox(tmp_path)
    photo = _photo(tmp_path, "2026/10/14/cam1-101500-0123abcd.jpg")
    event = WorkerEvent(
        camera_id=1,
        type=EventType.OBJECT_ENTERED,
        occurred_at=datetime.now(UTC),
        snapshot_path=photo,
    )
    report = outbox.add_event(event, epoch=5)
    assert _files(outbox.encode(report)) == [FRAME]
    assert outbox.pending() == [report]

    outbox.ack([report.seq])
    assert outbox.pending() == []
    assert not (tmp_path / photo).exists()


def test_the_outbox_keeps_the_latest_session_state(tmp_path: Path) -> None:
    outbox = Outbox(tmp_path)
    vehicle = _photo(tmp_path, f"sessions/2026/10/14/{'ab' * 16}-vehicle.jpg")
    state = SessionState(
        id="ab" * 16,
        camera_id=1,
        arrived_at=datetime.now(UTC),
        vehicle_snapshot_path=vehicle,
    )
    first = outbox.add_session(state, epoch=5)
    second = outbox.add_session(state, epoch=5)
    assert outbox.pending() == [second]
    assert not outbox.is_pending(first.seq)

    # The photo goes along until the server has it, then only when it changes.
    assert _files(outbox.encode(second)) == [FRAME]
    outbox.ack([second.seq])
    third = outbox.add_session(state, epoch=5)
    assert _files(outbox.encode(third)) == []
    os.utime(tmp_path / vehicle, ns=(0, 1))
    assert _files(outbox.encode(third)) == [FRAME]
    outbox.ack([third.seq])

    # Once the finished session is saved its photos go.
    done = outbox.add_session(
        SessionState(
            id=state.id,
            camera_id=1,
            arrived_at=state.arrived_at,
            status=SessionStatus.COMPLETED,
            vehicle_snapshot_path=vehicle,
        ),
        epoch=5,
    )
    outbox.encode(done)
    outbox.ack([done.seq])
    assert not (tmp_path / vehicle).exists()


def test_the_outbox_drops_the_oldest_events_when_full(tmp_path: Path) -> None:
    outbox = Outbox(tmp_path, limit=3)
    reports = [
        outbox.add_event(
            WorkerEvent(camera_id=1, type=EventType.OBJECT_ENTERED, occurred_at=datetime.now(UTC)),
            epoch=1,
        )
        for _ in range(5)
    ]
    assert outbox.pending() == reports[2:]
    assert outbox.dropped == 2


def test_reports_decode_on_the_server(tmp_path: Path) -> None:
    outbox = Outbox(tmp_path)
    report = outbox.add_event(
        WorkerEvent(camera_id=3, type=EventType.LINE_CROSSED_OUT, occurred_at=datetime.now(UTC)),
        epoch=9,
    )
    message, files = unpack(WORKER_MESSAGES, outbox.encode(report))
    assert isinstance(message, EventReport)
    assert message.event == report.event
    assert files == []
