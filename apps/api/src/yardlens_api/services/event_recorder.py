"""Consumes worker events on the event loop: persists them, broadcasts them and notifies.

A database that is briefly unreachable (a restart, a network blip) must not cost data: events and
loading sessions are kept and retried with backoff until they are saved. Rows the database
rejects outright (an event for a camera deleted in the meantime) are dropped one by one, so they
cannot hold the rest back. For sessions only the latest state matters: each update carries the
whole session, so a newer one replaces any update still waiting to be saved.

Events from workers may arrive twice (a worker sends again what was not acknowledged before its
connection dropped): each event has a ``uid`` and is saved once. ``on_saved`` callbacks tell the
sender when its event or session is safely stored, or was refused for good.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Any

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yardlens_api.core.enums import CameraStatus, EventType
from yardlens_api.db.models import Event
from yardlens_api.schemas.settings import TelegramSettings
from yardlens_api.services.camera_worker import WorkerEvent
from yardlens_api.services.event_bus import BusMessage, EventBus
from yardlens_api.services.events import to_event_read
from yardlens_api.services.loading import SessionState
from yardlens_api.services.loading_store import row_to_read, save_session
from yardlens_api.services.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100
_BATCH_WINDOW_SECONDS = 0.25
RETRY_INITIAL_SECONDS = 1.0
RETRY_MAX_SECONDS = 30.0
# Events this old when they are saved (held by a worker while it was cut off) are recorded
# without a Telegram message: news that late is noise.
NOTIFY_MAX_AGE = timedelta(minutes=5)

type Callback = Callable[[], None]


@dataclass(frozen=True, slots=True)
class _Queued:
    event: WorkerEvent
    on_saved: Callback | None = None


@dataclass(slots=True)
class _PendingSession:
    state: SessionState
    callbacks: list[Callback] = field(default_factory=list[Callback])


def _call(callbacks: Iterable[Callback | None]) -> None:
    for callback in callbacks:
        if callback is not None:
            try:
                callback()
            except Exception:
                logger.exception("Save callback failed")


class Outcome(Enum):
    SAVED = auto()
    DROPPED = auto()
    RETRY = auto()


def is_transient(exc: BaseException) -> bool:
    """Connection trouble worth retrying, as opposed to data the database refuses."""
    if isinstance(exc, IntegrityError):
        return False
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return isinstance(exc, OperationalError | InterfaceError | OSError | TimeoutError)


def next_delay(delay: float) -> float:
    return min(delay * 2, RETRY_MAX_SECONDS)


class EventRecorder:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        notifier: TelegramNotifier,
        telegram_settings: Callable[[], TelegramSettings],
        camera_name: Callable[[int], str],
        snapshots_dir: Path,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._notifier = notifier
        self._telegram_settings = telegram_settings
        self._camera_name = camera_name
        self._snapshots_dir = snapshots_dir
        self._queue: asyncio.Queue[_Queued] = asyncio.Queue(maxsize=10_000)
        self._task: asyncio.Task[None] | None = None
        self._sessions: asyncio.Queue[tuple[SessionState, Callback | None]] = asyncio.Queue(
            maxsize=1_000
        )
        self._sessions_task: asyncio.Task[None] | None = None
        self._notifications: set[asyncio.Task[bool]] = set()
        # Work taken off the queues but not saved yet; flushed on shutdown.
        self._inflight: list[_Queued] = []
        self._pending_sessions: dict[str, _PendingSession] = {}

    def submit(self, event: WorkerEvent, on_saved: Callback | None = None) -> None:
        """Enqueue an event. Must be called on the event loop thread. A full queue drops the
        event without calling ``on_saved``, so a worker sends it again."""
        try:
            self._queue.put_nowait(_Queued(event, on_saved))
        except asyncio.QueueFull:
            logger.warning("Event queue full; dropping event", extra={"type": event.type.value})

    def submit_session(self, state: SessionState, on_saved: Callback | None = None) -> None:
        """Enqueue a loading session update. Must be called on the event loop thread."""
        try:
            self._sessions.put_nowait((state, on_saved))
        except asyncio.QueueFull:
            logger.warning("Session queue full; dropping update", extra={"session_id": state.id})

    def publish_status(self, camera_id: int, status: CameraStatus, last_error: str | None) -> None:
        payload = {"camera_id": camera_id, "status": status.value, "last_error": last_error}
        self._bus.publish(BusMessage(kind="camera_status", data=json.dumps(payload)))

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="event-recorder")
        self._sessions_task = asyncio.create_task(self._run_sessions(), name="session-recorder")

    async def stop(self) -> None:
        for task in (self._task, self._sessions_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        # One last attempt each; whatever still fails is reported, not retried forever.
        batch = self._inflight + [self._queue.get_nowait() for _ in range(self._queue.qsize())]
        if batch and await self._persist(batch) is Outcome.RETRY:
            logger.error(
                "Database unreachable at shutdown; events lost", extra={"count": len(batch)}
            )
        self._take_session_updates()
        for pending in list(self._pending_sessions.values()):
            if await self._save_session(pending.state) is Outcome.RETRY:
                logger.error(
                    "Database unreachable at shutdown; session update lost",
                    extra={"session_id": pending.state.id},
                )
        if self._notifications:
            await asyncio.wait(self._notifications, timeout=5.0)

    # -- events --------------------------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            deadline = asyncio.get_running_loop().time() + _BATCH_WINDOW_SECONDS
            while len(batch) < _BATCH_SIZE:
                timeout = deadline - asyncio.get_running_loop().time()
                if timeout <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), timeout))
                except TimeoutError:
                    break
            self._inflight = batch
            delay = RETRY_INITIAL_SECONDS
            while await self._persist(batch) is Outcome.RETRY:
                logger.warning(
                    "Database unavailable; will retry saving events",
                    extra={"count": len(batch), "retry_in": delay},
                )
                await asyncio.sleep(delay)
                delay = next_delay(delay)
            self._inflight = []

    @staticmethod
    def _values(item: WorkerEvent) -> dict[str, Any]:
        values: dict[str, Any] = {
            "uid": item.uid,
            "camera_id": item.camera_id,
            "type": item.type.value,
            "class_name": item.class_name,
            "tracker_id": item.tracker_id,
            "confidence": item.confidence,
            "snapshot_path": item.snapshot_path,
            "created_at": item.occurred_at,
        }
        if item.details is not None:  # left out, the column stays NULL rather than JSON null
            values["details"] = item.details
        return values

    async def _insert(self, batch: list[_Queued]) -> list[Event]:
        """Insert the events not saved before; returns the new rows."""
        async with self._session_factory() as session:
            dialect = session.get_bind().dialect.name
            insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
            statement = (
                insert(Event).on_conflict_do_nothing(index_elements=["uid"]).returning(Event)
            )
            rows = list(
                await session.scalars(statement, [self._values(item.event) for item in batch])
            )
            await session.commit()
        return rows

    async def _persist(self, batch: list[_Queued]) -> Outcome:
        try:
            rows = await self._insert(batch)
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            if is_transient(exc):
                return Outcome.RETRY
            # Usually a camera deleted while its events were in flight (FK violation): save
            # the others one by one.
            rows = await self._persist_each(batch)
        _call(item.on_saved for item in batch)
        self._announce(rows)
        return Outcome.SAVED

    async def _persist_each(self, batch: list[_Queued]) -> list[Event]:
        saved: list[Event] = []
        for item in batch:
            try:
                saved.extend(await self._insert([item]))
            except SQLAlchemyError, OSError, TimeoutError:
                logger.warning(
                    "Dropped an event the database rejected",
                    extra={"camera_id": item.event.camera_id, "type": item.event.type.value},
                    exc_info=True,
                )
        return saved

    def _announce(self, rows: list[Event]) -> None:
        telegram = self._telegram_settings()
        stale = datetime.now(UTC) - NOTIFY_MAX_AGE
        for row in sorted(rows, key=lambda row: row.id):
            name = self._camera_name(row.camera_id)
            read = to_event_read(row, name)
            self._bus.publish(BusMessage(kind="event", data=read.model_dump_json()))
            if telegram.enabled and row.created_at >= stale:
                self._schedule_notification(telegram, row, name)

    # -- loading sessions ----------------------------------------------------------------------

    def _take_session_updates(self) -> None:
        """Move queued updates into the pending map; a newer state replaces an older one, and
        whoever waits on the older one learns of the save with the newer."""
        while not self._sessions.empty():
            state, on_saved = self._sessions.get_nowait()
            self._add_pending(state, on_saved)

    def _add_pending(self, state: SessionState, on_saved: Callback | None) -> None:
        pending = self._pending_sessions.get(state.id)
        if pending is None:
            pending = self._pending_sessions[state.id] = _PendingSession(state)
        pending.state = state
        if on_saved is not None:
            pending.callbacks.append(on_saved)

    async def _run_sessions(self) -> None:
        delay = RETRY_INITIAL_SECONDS
        while True:
            if not self._pending_sessions:
                self._add_pending(*await self._sessions.get())
            self._take_session_updates()
            session_id, pending = next(iter(self._pending_sessions.items()))
            # Only this task touches the pending map: a newer state arriving during the save
            # waits in the queue and comes next.
            outcome = await self._save_session(pending.state)
            if outcome is Outcome.RETRY:
                logger.warning(
                    "Database unavailable; will retry saving a loading session",
                    extra={"session_id": session_id, "retry_in": delay},
                )
                await asyncio.sleep(delay)
                delay = next_delay(delay)
                continue
            delay = RETRY_INITIAL_SECONDS
            del self._pending_sessions[session_id]
            _call(pending.callbacks)

    async def _save_session(self, state: SessionState) -> Outcome:
        try:
            async with self._session_factory() as session:
                row = await save_session(session, state)
                read = row_to_read(row, self._camera_name(state.camera_id))
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            if is_transient(exc):
                return Outcome.RETRY
            logger.warning(
                "Dropped a loading session the database rejected",
                extra={"session_id": state.id},
                exc_info=True,
            )
            return Outcome.DROPPED
        self._bus.publish(BusMessage(kind="session", data=read.model_dump_json()))
        return Outcome.SAVED

    def _schedule_notification(self, settings: TelegramSettings, row: Event, name: str) -> None:
        snapshot = self._snapshots_dir / row.snapshot_path if row.snapshot_path else None
        task = asyncio.create_task(
            self._notifier.notify_event(
                settings,
                camera_id=row.camera_id,
                camera_name=name,
                event_type=EventType(row.type),
                class_name=row.class_name,
                snapshot=snapshot,
                details=row.details,
            )
        )
        self._notifications.add(task)
        task.add_done_callback(self._notifications.discard)
