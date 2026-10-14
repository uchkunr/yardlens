"""Workers: tokens, the channel, placement, failover and what the server accepts from them.

A scripted worker talks to a real server over a real WebSocket; the camera code itself does not
run (this server's processing is off and the scripted worker only says what a real one would).
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from tests.conftest import FakeDetector, browser, login, serve
from yardlens_api.api.deps import Container
from yardlens_api.core.config import Settings
from yardlens_api.core.enums import CameraStatus, EventType, SessionStatus
from yardlens_api.db.models import Event, LoadingSession
from yardlens_api.main import create_app
from yardlens_api.schemas.worker_protocol import (
    CLOSE_UNAUTHORIZED,
    MAX_FILE_BYTES,
    PROTOCOL_VERSION,
    SERVER_MESSAGES,
    Ack,
    Assign,
    EventReport,
    Hello,
    ProtocolError,
    SessionReport,
    SettingsUpdate,
    Status,
    Welcome,
    pack,
    unpack,
)
from yardlens_api.schemas.workers import WorkerInfo
from yardlens_api.services.camera_worker import WorkerEvent
from yardlens_api.services.cluster import WORKER_OFFLINE, Placement
from yardlens_api.services.loading import SessionState

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
INFO = WorkerInfo(
    hostname="worker-pc",
    platform="Linux",
    version="0.0.0",
    logical_cpus=8,
    physical_cores=4,
    memory_total_bytes=16 * 1024**3,
    backend="openvino",
    device="intel:cpu",
)


@pytest.fixture
def server(live_server: str) -> str:
    """The server's ws:// base address."""
    return "ws" + live_server.removeprefix("http")


class ScriptedWorker:
    def __init__(self, socket: ClientConnection) -> None:
        self.socket = socket
        self.seq = 0

    async def send(self, message: BaseModel, files: Sequence[bytes] = ()) -> None:
        await self.socket.send(pack(message, files))

    async def expect[T: BaseModel](self, kind: type[T], within: float = 5.0) -> T:
        """The next message of this kind; others (pings, watch lists) are skipped."""
        async with asyncio.timeout(within):
            while True:
                message, _ = unpack(SERVER_MESSAGES, await self.socket.recv())
                if isinstance(message, kind):
                    return message

    async def ack_for(self, seq: int) -> None:
        while seq not in (await self.expect(Ack)).seqs:
            pass

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq


@contextlib.asynccontextmanager
async def scripted_worker(
    server: str, token: str, running: dict[int, int] | None = None
) -> AsyncGenerator[tuple[ScriptedWorker, Assign]]:
    async with connect(
        f"{server}/api/workers/connect", additional_headers={"Authorization": f"Bearer {token}"}
    ) as socket:
        worker = ScriptedWorker(socket)
        hello = Hello(
            protocol=PROTOCOL_VERSION, info=INFO, running=running or {}, time=datetime.now(UTC)
        )
        await worker.send(hello)
        await worker.expect(Welcome)
        yield worker, await worker.expect(Assign)


async def add_worker(client: httpx.AsyncClient, name: str = "Склад-2") -> tuple[int, str]:
    response = await client.post("/api/workers", json={"name": name})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["worker"]["id"], body["token"]


async def add_camera(client: httpx.AsyncClient, name: str = "Gate", **extra: Any) -> int:
    payload = {"name": name, "source_type": "rtsp", "url": "rtsp://192.0.2.10/stream", **extra}
    response = await client.post("/api/cameras", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def local_processing(client: httpx.AsyncClient, *, enabled: bool) -> None:
    response = await client.put("/api/workers/local", json={"enabled": enabled})
    assert response.status_code == 200, response.text


async def wait_for(condition: Callable[[], bool]) -> None:
    for _ in range(250):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


# -- protocol ------------------------------------------------------------------------------------


def test_messages_with_files_round_trip() -> None:
    message = Ack(seqs=[1, 2])
    frame = pack(message, [b"one", b"", b"three"])
    decoded, files = unpack(SERVER_MESSAGES, frame)
    assert decoded == message
    assert files == [b"one", b"", b"three"]
    assert unpack(SERVER_MESSAGES, pack(message)) == (message, [])


@pytest.mark.parametrize(
    "frame",
    [
        b"\x00\x00",  # shorter than the length prefix
        b"\x00\x00\x00\x05[1,2]",  # header is not an object
        b'\x00\x00\x00\x24{"type":"ack","seqs":[],"files":[10]}',  # files missing
        b'\x00\x00\x00\x23{"type":"ack","seqs":[],"files":[-1]}',
        '{"type":"nonsense"}',
        "not json",
    ],
)
def test_malformed_frames_are_refused(frame: str | bytes) -> None:
    with pytest.raises(ProtocolError):
        unpack(SERVER_MESSAGES, frame)


def test_files_over_the_limit_are_refused() -> None:
    header = b'{"type":"ack","seqs":[],"files":[%d]}' % (MAX_FILE_BYTES + 1)
    frame = len(header).to_bytes(4, "big") + header
    with pytest.raises(ProtocolError):
        unpack(SERVER_MESSAGES, frame)


# -- tokens --------------------------------------------------------------------------------------


async def test_a_new_worker_comes_with_its_token_and_commands(
    client: httpx.AsyncClient, server: str
) -> None:
    response = await client.post("/api/workers", json={"name": "Склад-2"})
    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith(f"ylw_{body['worker']['id']}_")
    assert body["token"] in body["commands"]["cpu"]
    assert "--gpus all" in body["commands"]["gpu"]
    assert "-cuda" in body["commands"]["gpu"]

    listed = (await client.get("/api/workers")).json()
    assert [worker["name"] for worker in listed["workers"]] == ["Склад-2"]
    assert listed["workers"][0]["online"] is False
    # The token is never shown again.
    assert "token" not in str(listed)
    assert (await client.post("/api/workers", json={"name": "Склад-2"})).status_code == 409


@pytest.mark.parametrize("token", ["", "ylw_1_wrong", "ylw_999_x", "Bearer-less"])
async def test_unknown_tokens_cannot_connect(
    client: httpx.AsyncClient, server: str, token: str
) -> None:
    await add_worker(client)
    with pytest.raises(InvalidStatus) as refused:
        async with connect(
            f"{server}/api/workers/connect",
            additional_headers={"Authorization": f"Bearer {token}"},
        ):
            pass
    assert refused.value.response.status_code == 403


async def test_worker_endpoints_need_a_signed_in_user(anon_client: httpx.AsyncClient) -> None:
    assert (await anon_client.get("/api/workers")).status_code == 401
    assert (await anon_client.post("/api/workers", json={"name": "x"})).status_code == 401


# -- placement and reports -----------------------------------------------------------------------


async def test_a_worker_takes_the_cameras_waiting_for_a_node(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    await local_processing(client, enabled=False)
    camera_id = await add_camera(client)
    waiting = (await client.get(f"/api/cameras/{camera_id}")).json()
    assert waiting["node"] is None
    assert waiting["runtime"]["status"] == "offline"
    assert waiting["runtime"]["last_error"] == "No worker is available to process this camera"

    worker_id, token = await add_worker(client)
    async with scripted_worker(server, token) as (worker, assign):
        assert [item.config.id for item in assign.cameras] == [camera_id]
        epoch = assign.cameras[0].epoch
        await worker.send(Status(camera_id=camera_id, epoch=epoch, status=CameraStatus.ONLINE))
        await wait_for(lambda: container.cameras.runtime(camera_id).status is CameraStatus.ONLINE)

        camera = (await client.get(f"/api/cameras/{camera_id}")).json()
        assert camera["node"] == {"worker_id": worker_id, "worker_name": "Склад-2"}
        assert camera["runtime"]["status"] == "online"
        listed = (await client.get("/api/workers")).json()
        assert listed["workers"][0]["online"] is True
        assert listed["workers"][0]["cameras"] == [camera_id]
        assert listed["workers"][0]["info"]["hostname"] == "worker-pc"
        assert listed["unplaced_cameras"] == []

    # Gone: its camera waits for it.
    await wait_for(lambda: container.cameras.node(worker_id) is None)
    camera = (await client.get(f"/api/cameras/{camera_id}")).json()
    assert camera["runtime"]["last_error"] == "The worker processing this camera is offline"


async def test_events_from_a_worker_are_saved_once_with_their_photo(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    await local_processing(client, enabled=False)
    camera_id = await add_camera(client)
    _, token = await add_worker(client)
    async with scripted_worker(server, token) as (worker, assign):
        epoch = assign.cameras[0].epoch
        path = f"2026/10/14/cam{camera_id}-101500-0123abcd.jpg"
        event = WorkerEvent(
            camera_id=camera_id,
            type=EventType.LINE_CROSSED_IN,
            occurred_at=datetime.now(UTC),
            class_name="truck",
            snapshot_path=path,
        )
        for _ in range(2):  # the second time, as after a dropped connection
            seq = worker.next_seq()
            await worker.send(EventReport(seq=seq, epoch=epoch, event=event), [JPEG])
            await worker.ack_for(seq)

    async with container.session_factory() as session:
        rows = (await session.scalars(select(Event).where(Event.uid == event.uid))).all()
    assert len(rows) == 1
    assert rows[0].snapshot_path == path
    assert (container.settings.snapshots_dir / path).read_bytes() == JPEG


@pytest.mark.parametrize(
    "path",
    [
        "../../outside.jpg",
        "2026/10/14/cam999-101500-0123abcd.jpg",  # another camera's name
        "sessions/2026/10/14/aaaa-vehicle.jpg",
        "2026/10/14/cam1-101500-0123abcd.png",
    ],
)
async def test_photos_under_unexpected_paths_are_refused(
    client: httpx.AsyncClient, server: str, container: Container, path: str
) -> None:
    await local_processing(client, enabled=False)
    camera_id = await add_camera(client)
    _, token = await add_worker(client)
    async with scripted_worker(server, token) as (worker, assign):
        event = WorkerEvent(
            camera_id=camera_id,
            type=EventType.OBJECT_ENTERED,
            occurred_at=datetime.now(UTC),
            snapshot_path=path,
        )
        seq = worker.next_seq()
        await worker.send(EventReport(seq=seq, epoch=assign.cameras[0].epoch, event=event), [JPEG])
        await worker.ack_for(seq)

    async with container.session_factory() as session:
        row = await session.scalar(select(Event).where(Event.uid == event.uid))
    assert row is not None
    assert row.snapshot_path is None  # the event is kept, the photo is not
    data_dir = container.settings.data_dir
    assert not (data_dir.parent / "outside.jpg").exists()
    assert not list(container.settings.snapshots_dir.rglob("*.jpg"))


async def test_session_reports_under_an_old_epoch_are_ignored(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    await local_processing(client, enabled=False)
    camera_id = await add_camera(client)
    _, token = await add_worker(client)
    arrived = datetime.now(UTC) - timedelta(minutes=2)
    session_id = "ab" * 16
    vehicle = f"sessions/{arrived:%Y/%m/%d}/{session_id}-vehicle.jpg"
    state = SessionState(
        id=session_id,
        camera_id=camera_id,
        arrived_at=arrived,
        loaded=3,
        vehicle_class="truck",
        vehicle_snapshot_path=vehicle,
    )
    async with scripted_worker(server, token) as (worker, assign):
        epoch = assign.cameras[0].epoch
        stale = worker.next_seq()
        await worker.send(SessionReport(seq=stale, epoch=epoch - 1, session=state))
        await worker.ack_for(stale)
        async with container.session_factory() as session:
            assert await session.get(LoadingSession, session_id) is None

        current = worker.next_seq()
        await worker.send(
            SessionReport(seq=current, epoch=epoch, session=state, images=[vehicle]), [JPEG]
        )
        await worker.ack_for(current)

    async with container.session_factory() as session:
        row = await session.get(LoadingSession, session_id)
    assert row is not None
    assert row.loaded == 3
    assert row.status == SessionStatus.ACTIVE.value
    assert row.vehicle_snapshot_path == vehicle
    assert (container.settings.snapshots_dir / vehicle).read_bytes() == JPEG


async def test_a_wrong_worker_clock_is_corrected(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    await local_processing(client, enabled=False)
    camera_id = await add_camera(client)
    _, token = await add_worker(client)
    behind = timedelta(minutes=7)
    async with connect(
        f"{server}/api/workers/connect", additional_headers={"Authorization": f"Bearer {token}"}
    ) as socket:
        worker = ScriptedWorker(socket)
        now = datetime.now(UTC)
        await worker.send(Hello(protocol=PROTOCOL_VERSION, info=INFO, time=now - behind))
        assign = await worker.expect(Assign)
        happened = datetime.now(UTC) - behind
        event = WorkerEvent(
            camera_id=camera_id, type=EventType.OBJECT_ENTERED, occurred_at=happened
        )
        seq = worker.next_seq()
        await worker.send(EventReport(seq=seq, epoch=assign.cameras[0].epoch, event=event))
        await worker.ack_for(seq)

    async with container.session_factory() as session:
        row = await session.scalar(select(Event).where(Event.uid == event.uid))
    assert row is not None
    assert abs(row.created_at - (happened + behind)) < timedelta(seconds=2)


async def test_settings_changes_reach_connected_workers(
    client: httpx.AsyncClient, server: str
) -> None:
    _, token = await add_worker(client)
    async with scripted_worker(server, token) as (worker, _):
        current = (await client.get("/api/settings")).json()["performance"]
        response = await client.put(
            "/api/settings", json={"performance": current | {"cpu_limit_percent": 55}}
        )
        assert response.status_code == 200
        update = await worker.expect(SettingsUpdate)
        assert update.settings.performance.cpu_limit_percent == 55


# -- removal and failover ------------------------------------------------------------------------


async def test_a_removed_worker_is_cut_off_and_its_cameras_move(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    await local_processing(client, enabled=False)
    camera_id = await add_camera(client)
    worker_id, token = await add_worker(client)
    async with scripted_worker(server, token) as (worker, assign):
        assert assign.cameras
        assert (await client.delete(f"/api/workers/{worker_id}")).status_code == 204
        with pytest.raises(ConnectionClosed) as closed:
            await worker.expect(Welcome)
        assert closed.value.rcvd is not None
        assert closed.value.rcvd.code == CLOSE_UNAUTHORIZED
    assert container.cameras.placement(camera_id) is None  # nowhere left to run
    with pytest.raises(InvalidStatus):
        async with scripted_worker(server, token):
            pass


async def test_cameras_move_only_after_the_lease(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    container.cameras.lease_seconds = 0.5
    await local_processing(client, enabled=False)
    first_id, first_token = await add_worker(client, "first")
    second_id, second_token = await add_worker(client, "second")
    async with scripted_worker(server, first_token) as (first, _):
        camera_id = await add_camera(client)
        assign = await first.expect(Assign)  # the new camera goes to the only worker
        epoch = assign.cameras[0].epoch
    assert container.cameras.placement(camera_id) == Placement(first_id, epoch)

    async with scripted_worker(server, second_token) as (second, assign):
        # Within the lease the camera waits for its worker.
        assert assign.cameras == []
        assign = await second.expect(Assign, within=3.0)
        assert [item.config.id for item in assign.cameras] == [camera_id]
        assert assign.cameras[0].epoch > epoch
        placement = container.cameras.placement(camera_id)
        assert placement is not None
        assert placement.worker_id == second_id

        # The first worker comes back still running it: it is told to let go.
        async with scripted_worker(server, first_token, running={camera_id: epoch}) as (
            _,
            back,
        ):
            assert back.cameras == []
            assert back.stop == []  # handed over, not stopped for good


async def test_rebalance_spreads_cameras_over_the_workers(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    await local_processing(client, enabled=False)
    first_id, first_token = await add_worker(client, "first")
    second_id, second_token = await add_worker(client, "second")
    async with scripted_worker(server, first_token):
        cameras = [await add_camera(client, f"Camera {index}") for index in range(4)]
        await wait_for(lambda: len(container.cameras.cameras_on(first_id)) == 4)
        async with scripted_worker(server, second_token):
            # A new worker takes nothing by itself: moving resets tracking.
            assert container.cameras.cameras_on(second_id) == []
            response = await client.post("/api/workers/rebalance")
            assert response.status_code == 200
            body = response.json()
            counts = sorted(len(worker["cameras"]) for worker in body["workers"])
            assert counts == [2, 2]
            assert (
                sorted(camera for worker in body["workers"] for camera in worker["cameras"])
                == cameras
            )


async def test_pinned_cameras_wait_for_their_worker(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    worker_id, _ = await add_worker(client)
    camera_id = await add_camera(client, worker_id=worker_id)
    camera = (await client.get(f"/api/cameras/{camera_id}")).json()
    assert camera["worker_id"] == worker_id
    assert camera["node"]["worker_id"] == worker_id  # placed there, though not connected
    assert camera["runtime"]["last_error"] == "The worker processing this camera is offline"
    assert container.cameras.local.worker(camera_id) is None

    video = await client.post(
        "/api/cameras",
        json={"name": "Clip", "source_type": "file", "url": "clip.mp4", "worker_id": worker_id},
    )
    assert video.status_code == 422


async def test_turning_local_processing_off_moves_cameras_to_workers(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    camera_id = await add_camera(client)
    placement = container.cameras.placement(camera_id)
    assert placement is not None
    assert placement.worker_id is None  # this server
    assert container.cameras.local.worker(camera_id) is not None

    worker_id, token = await add_worker(client)
    async with scripted_worker(server, token) as (worker, assign):
        assert assign.cameras == []
        await local_processing(client, enabled=False)
        moved = await worker.expect(Assign)
        assert [item.config.id for item in moved.cameras] == [camera_id]
        assert container.cameras.local.worker(camera_id) is None
        placement = container.cameras.placement(camera_id)
        assert placement is not None
        assert placement.worker_id == worker_id


async def test_a_disabled_camera_closes_its_session_on_the_worker(
    client: httpx.AsyncClient, server: str, container: Container
) -> None:
    await local_processing(client, enabled=False)
    camera_id = await add_camera(client)
    _, token = await add_worker(client)
    session_id = "cd" * 16
    state = SessionState(id=session_id, camera_id=camera_id, arrived_at=datetime.now(UTC), loaded=2)
    async with scripted_worker(server, token) as (worker, assign):
        epoch = assign.cameras[0].epoch
        seq = worker.next_seq()
        await worker.send(SessionReport(seq=seq, epoch=epoch, session=state))
        await worker.ack_for(seq)

        response = await client.post(f"/api/cameras/{camera_id}/disable")
        assert response.status_code == 200
        stopped = await worker.expect(Assign)
        assert stopped.cameras == []
        assert stopped.stop == [camera_id]  # for good: the worker closes the session

        state.status = SessionStatus.COMPLETED
        state.departed_at = datetime.now(UTC)
        seq = worker.next_seq()
        await worker.send(SessionReport(seq=seq, epoch=epoch, session=state))
        await worker.ack_for(seq)

    async with container.session_factory() as session:
        row = await session.get(LoadingSession, session_id)
    assert row is not None
    assert row.status == SessionStatus.COMPLETED.value


async def test_a_restart_moves_no_camera(settings: Settings) -> None:
    first = create_app(settings, detector=FakeDetector())
    async with first.router.lifespan_context(first), browser(first) as client:
        await login(client)
        await local_processing(client, enabled=False)
        camera_id = await add_camera(client)
        worker_id, token = await add_worker(client)
        async with (
            serve(first) as url,
            scripted_worker("ws" + url.removeprefix("http"), token) as (_, assign),
        ):
            epoch = assign.cameras[0].epoch

    second = create_app(settings, detector=FakeDetector())
    async with second.router.lifespan_context(second):
        container: Container = second.state.container
        # The worker gets the lease to come back before its cameras move.
        assert container.cameras.placement(camera_id) == Placement(worker_id, epoch)
        assert container.cameras.runtime(camera_id).last_error == WORKER_OFFLINE
