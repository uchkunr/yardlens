"""Consumes worker events on the event loop: persists them, broadcasts them and notifies."""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yardlens_api.core.enums import CameraStatus, EventType
from yardlens_api.db.models import Event
from yardlens_api.schemas.settings import TelegramSettings
from yardlens_api.services.camera_worker import WorkerEvent
from yardlens_api.services.event_bus import BusMessage, EventBus
from yardlens_api.services.events import to_event_read
from yardlens_api.services.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100
_BATCH_WINDOW_SECONDS = 0.25


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
        self._notifications: set[asyncio.Task[bool]] = set()

    def submit(self, event: WorkerEvent) -> None:
        """Enqueue an event. Must be called on the event loop thread."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full; dropping event", extra={"type": event.type.value})

    def publish_status(self, camera_id: int, status: CameraStatus, last_error: str | None) -> None:
        payload = {"camera_id": camera_id, "status": status.value, "last_error": last_error}
        self._bus.publish(BusMessage(kind="camera_status", data=json.dumps(payload)))

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="event-recorder")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._flush_remaining()
        if self._notifications:
            await asyncio.wait(self._notifications, timeout=5.0)

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
            await self._persist(batch)

    async def _flush_remaining(self) -> None:
        batch: list[WorkerEvent] = []
        while not self._queue.empty():
            batch.append(self._queue.get_nowait())
        if batch:
            await self._persist(batch)

    async def _persist(self, batch: list[WorkerEvent]) -> None:
        rows = [
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
        try:
            async with self._session_factory() as session:
                session.add_all(rows)
                await session.commit()
        except SQLAlchemyError:
            # Usually a camera deleted while its events were in flight (FK violation).
            logger.warning("Could not persist events", extra={"count": len(rows)}, exc_info=True)
            return

        telegram = self._telegram_settings()
        for row in rows:
            name = self._camera_name(row.camera_id)
            read = to_event_read(row, name)
            self._bus.publish(BusMessage(kind="event", data=read.model_dump_json()))
            if telegram.enabled:
                self._schedule_notification(telegram, row, name)

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
            )
        )
        self._notifications.add(task)
        task.add_done_callback(self._notifications.discard)
