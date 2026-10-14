"""Consumes worker events on the event loop: persists them, broadcasts them and notifies.

A database that is briefly unreachable (a restart, a network blip) must not cost data: events and
loading sessions are kept and retried with backoff until they are saved. Rows the database
rejects outright (an event for a camera deleted in the meantime) are dropped one by one, so they
cannot hold the rest back. For sessions only the latest state matters: each update carries the
whole session, so a newer one replaces any update still waiting to be saved.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path

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
        self._queue: asyncio.Queue[WorkerEvent] = asyncio.Queue(maxsize=10_000)
        self._task: asyncio.Task[None] | None = None
        self._sessions: asyncio.Queue[SessionState] = asyncio.Queue(maxsize=1_000)
        self._sessions_task: asyncio.Task[None] | None = None
        self._notifications: set[asyncio.Task[bool]] = set()
        # Work taken off the queues but not saved yet; flushed on shutdown.
        self._inflight: list[WorkerEvent] = []
        self._pending_sessions: dict[str, SessionState] = {}

    def submit(self, event: WorkerEvent) -> None:
        """Enqueue an event. Must be called on the event loop thread."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full; dropping event", extra={"type": event.type.value})

    def submit_session(self, state: SessionState) -> None:
        """Enqueue a loading session update. Must be called on the event loop thread."""
        try:
            self._sessions.put_nowait(state)
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
        for state in list(self._pending_sessions.values()):
            if await self._save_session(state) is Outcome.RETRY:
                logger.error(
                    "Database unreachable at shutdown; session update lost",
                    extra={"session_id": state.id},
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
    def _rows(batch: list[WorkerEvent]) -> list[Event]:
        return [
            Event(
                camera_id=item.camera_id,
                type=item.type.value,
                class_name=item.class_name,
                tracker_id=item.tracker_id,
                confidence=item.confidence,
                snapshot_path=item.snapshot_path,
                details=item.details,
                created_at=item.occurred_at,
            )
            for item in batch
        ]

    async def _persist(self, batch: list[WorkerEvent]) -> Outcome:
        rows = self._rows(batch)
        try:
            async with self._session_factory() as session:
                session.add_all(rows)
                await session.commit()
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            if is_transient(exc):
                return Outcome.RETRY
            # Usually a camera deleted while its events were in flight (FK violation): save
            # the others one by one.
            rows = await self._persist_each(batch)
        self._announce(rows)
        return Outcome.SAVED

    async def _persist_each(self, batch: list[WorkerEvent]) -> list[Event]:
        saved: list[Event] = []
        for row in self._rows(batch):
            try:
                async with self._session_factory() as session:
                    session.add(row)
                    await session.commit()
            except SQLAlchemyError, OSError, TimeoutError:
                logger.warning(
                    "Dropped an event the database rejected",
                    extra={"camera_id": row.camera_id, "type": row.type},
                    exc_info=True,
                )
            else:
                saved.append(row)
        return saved

    def _announce(self, rows: list[Event]) -> None:
        telegram = self._telegram_settings()
        for row in rows:
            name = self._camera_name(row.camera_id)
            read = to_event_read(row, name)
            self._bus.publish(BusMessage(kind="event", data=read.model_dump_json()))
            if telegram.enabled:
                self._schedule_notification(telegram, row, name)

    # -- loading sessions ----------------------------------------------------------------------

    def _take_session_updates(self) -> None:
        """Move queued updates into the pending map; a newer state replaces an older one."""
        while not self._sessions.empty():
            state = self._sessions.get_nowait()
            self._pending_sessions[state.id] = state

    async def _run_sessions(self) -> None:
        delay = RETRY_INITIAL_SECONDS
        while True:
            if not self._pending_sessions:
                state = await self._sessions.get()
                self._pending_sessions[state.id] = state
            self._take_session_updates()
            session_id, state = next(iter(self._pending_sessions.items()))
            outcome = await self._save_session(state)
            if outcome is Outcome.RETRY:
                logger.warning(
                    "Database unavailable; will retry saving a loading session",
                    extra={"session_id": session_id, "retry_in": delay},
                )
                await asyncio.sleep(delay)
                delay = next_delay(delay)
                continue
            delay = RETRY_INITIAL_SECONDS
            # A newer state that arrived during the save is still queued and comes next.
            if self._pending_sessions.get(session_id) is state:
                del self._pending_sessions[session_id]

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
