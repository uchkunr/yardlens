"""Losing nothing to a database outage or an API restart."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_loading import START, ZONE, at, tick
from yardlens_api.api.deps import Container
from yardlens_api.core.enums import EventType, SessionStatus
from yardlens_api.db.models import Event, LoadingSession
from yardlens_api.services import event_recorder
from yardlens_api.services.camera_worker import WorkerEvent
from yardlens_api.services.event_recorder import EventRecorder, is_transient
from yardlens_api.services.loading import (
    HEARTBEAT_SECONDS,
    LoadingTracker,
    SessionChange,
    SessionState,
)
from yardlens_api.services.loading_store import (
    RESUME_WINDOW,
    purge_session_images_before,
    save_session,
    take_interrupted_sessions,
)


class FlakyFactory:
    """A session factory whose first ``failures`` sessions cannot reach the database."""

    def __init__(
        self, real: Callable[[], AbstractAsyncContextManager[AsyncSession]], failures: int
    ) -> None:
        self._real = real
        self.failures = failures

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        if self.failures > 0:
            self.failures -= 1
            raise OperationalError("SELECT 1", {}, ConnectionRefusedError("database is down"))
        return self._real()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(event_recorder, "RETRY_INITIAL_SECONDS", 0.01)


async def _camera(client: httpx.AsyncClient) -> int:
    created = await client.post(
        "/api/cameras",
        json={"name": "Bay", "source_type": "file", "url": "videos/clip.mp4", "enabled": False},
    )
    assert created.status_code == 201, created.text
    camera_id: int = created.json()["id"]
    return camera_id


def _recorder(container: Container, factory: Any) -> EventRecorder:
    return EventRecorder(
        session_factory=factory,
        bus=container.bus,
        notifier=container.notifier,
        telegram_settings=container.settings_store.telegram,
        camera_name=lambda _: "Bay",
        snapshots_dir=container.settings.snapshots_dir,
    )


async def _until(condition: Callable[[], Any]) -> None:
    for _ in range(250):
        if await condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_connection_errors_are_retried_rejections_are_not() -> None:
    assert is_transient(OperationalError("x", {}, ConnectionRefusedError()))
    assert is_transient(ConnectionResetError())
    from sqlalchemy.exc import IntegrityError

    assert not is_transient(IntegrityError("x", {}, Exception("FOREIGN KEY constraint failed")))


@pytest.mark.usefixtures("video_file")
async def test_events_survive_a_database_outage(
    client: httpx.AsyncClient, container: Container
) -> None:
    camera_id = await _camera(client)
    flaky = FlakyFactory(container.session_factory, failures=3)
    recorder = _recorder(container, flaky)
    recorder.start()
    recorder.submit(
        WorkerEvent(
            camera_id=camera_id,
            type=EventType.OBJECT_ENTERED,
            occurred_at=datetime.now(UTC),
            class_name="truck",
        )
    )

    async def saved() -> bool:
        async with container.session_factory() as session:
            return bool((await session.scalars(select(Event))).all())

    try:
        await _until(saved)
    finally:
        await recorder.stop()
    assert flaky.failures == 0


@pytest.mark.usefixtures("video_file")
async def test_rejected_events_do_not_hold_back_the_rest(
    client: httpx.AsyncClient, container: Container
) -> None:
    camera_id = await _camera(client)
    recorder = _recorder(container, container.session_factory)
    now = datetime.now(UTC)
    gone = WorkerEvent(camera_id=999, type=EventType.OBJECT_ENTERED, occurred_at=now)
    kept = WorkerEvent(camera_id=camera_id, type=EventType.OBJECT_ENTERED, occurred_at=now)
    recorder.start()
    recorder.submit(gone)
    recorder.submit(kept)

    async def saved() -> bool:
        async with container.session_factory() as session:
            return bool((await session.scalars(select(Event))).all())

    try:
        await _until(saved)
    finally:
        await recorder.stop()
    async with container.session_factory() as session:
        rows = (await session.scalars(select(Event))).all()
    assert [row.camera_id for row in rows] == [camera_id]


@pytest.mark.usefixtures("video_file")
async def test_the_latest_session_state_wins_after_an_outage(
    client: httpx.AsyncClient, container: Container
) -> None:
    camera_id = await _camera(client)
    flaky = FlakyFactory(container.session_factory, failures=4)
    recorder = _recorder(container, flaky)
    state = SessionState(id="s1", camera_id=camera_id, arrived_at=START)
    recorder.start()
    for loaded in (1, 2, 5):
        recorder.submit_session(
            SessionState(id="s1", camera_id=camera_id, arrived_at=START, loaded=loaded)
        )
    recorder.submit_session(
        SessionState(
            id="s1",
            camera_id=camera_id,
            arrived_at=START,
            loaded=7,
            status=SessionStatus.COMPLETED,
            departed_at=at(600),
        )
    )

    async def completed() -> bool:
        async with container.session_factory() as session:
            row = await session.get(LoadingSession, state.id)
            return row is not None and row.status == SessionStatus.COMPLETED.value

    try:
        await _until(completed)
    finally:
        await recorder.stop()
    async with container.session_factory() as session:
        row = await session.get(LoadingSession, "s1")
        assert row is not None
        assert row.loaded == 7


# -- resuming after a restart -------------------------------------------------------------------


def test_a_resumed_session_carries_on_when_the_vehicle_is_still_there() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=10, depart_after=60)
    saved = SessionState(id="s1", camera_id=1, arrived_at=START, loaded=12)
    tracker.resume(saved, t=1000.0, last_seen=at(900))
    update = tick(tracker, 1001.0, loaded=1)
    assert update is not None
    assert update.change is SessionChange.UPDATED
    assert update.session.id == "s1"
    assert update.session.loaded == 13
    # Still there: the session stays open past the departure delay.
    for second in range(1002, 1100, 5):
        tick(tracker, float(second))
    assert tracker.session_open


def test_a_resumed_session_closes_as_of_its_last_save_if_the_vehicle_is_gone() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=10, depart_after=60)
    saved = SessionState(id="s1", camera_id=1, arrived_at=START, loaded=12)
    tracker.resume(saved, t=1000.0, last_seen=at(900))
    closed = None
    for second in range(1000, 1100, 5):
        closed = tick(tracker, float(second), truck=False) or closed
    assert closed is not None
    assert closed.change is SessionChange.CLOSED
    assert closed.session.departed_at == at(900)
    assert closed.session.loaded == 12


def test_a_parked_vehicle_is_reported_every_minute() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=5, depart_after=60)
    changes = [update.change for second in range(0, 200, 5) if (update := tick(tracker, second))]
    assert changes[0] is SessionChange.OPENED
    # Nothing else happens, yet the session is re-saved about once a minute.
    assert changes.count(SessionChange.UPDATED) == int(195 // HEARTBEAT_SECONDS)


@pytest.mark.usefixtures("video_file")
async def test_recent_sessions_are_resumed_and_stale_ones_closed(
    client: httpx.AsyncClient, container: Container
) -> None:
    running = await _camera(client)
    stopped = await _camera_named(client, "Gate")
    now = datetime.now(UTC)
    async with container.session_factory() as session:
        for session_id, camera_id in (("fresh", running), ("old", running), ("off", stopped)):
            await save_session(
                session, SessionState(id=session_id, camera_id=camera_id, arrived_at=START)
            )
        old = await session.get(LoadingSession, "old")
        assert old is not None
        old.updated_at = now - RESUME_WINDOW - timedelta(minutes=1)
        await session.commit()

        resumable, closed = await take_interrupted_sessions(
            session, running_cameras={running}, now=now
        )
    assert list(resumable) == [running]
    assert resumable[running].state.id == "fresh"
    assert closed == 2
    async with container.session_factory() as session:
        stale = await session.get(LoadingSession, "old")
        assert stale is not None
        assert stale.status == SessionStatus.COMPLETED.value
        assert stale.departed_at == now - RESUME_WINDOW - timedelta(minutes=1)


async def _camera_named(client: httpx.AsyncClient, name: str) -> int:
    created = await client.post(
        "/api/cameras",
        json={"name": name, "source_type": "file", "url": "videos/clip.mp4", "enabled": False},
    )
    assert created.status_code == 201, created.text
    camera_id: int = created.json()["id"]
    return camera_id


@pytest.mark.usefixtures("video_file")
async def test_old_session_photos_are_forgotten_but_the_sessions_stay(
    client: httpx.AsyncClient, container: Container
) -> None:
    camera_id = await _camera(client)
    async with container.session_factory() as session:
        # 100 days back: older than this test's cutoff, newer than the 365 days the app purges.
        for session_id, arrived in (("old", START - timedelta(days=100)), ("recent", START)):
            await save_session(
                session,
                SessionState(
                    id=session_id,
                    camera_id=camera_id,
                    arrived_at=arrived,
                    vehicle_snapshot_path=f"sessions/{session_id}-vehicle.jpg",
                    plate_snapshot_path=f"sessions/{session_id}-plate.jpg",
                ),
            )
        paths = await purge_session_images_before(session, START - timedelta(days=30))
    assert paths == ["sessions/old-vehicle.jpg", "sessions/old-plate.jpg"]
    async with container.session_factory() as session:
        old = await session.get(LoadingSession, "old")
        recent = await session.get(LoadingSession, "recent")
        assert old is not None
        assert recent is not None
        assert (old.vehicle_snapshot_path, old.plate_snapshot_path) == (None, None)
        assert recent.vehicle_snapshot_path == "sessions/recent-vehicle.jpg"
