"""Owns the lifecycle of all camera workers and bridges worker threads to the event loop."""

import asyncio
import logging
from collections.abc import Callable

from yardlens_api.core.enums import CameraStatus
from yardlens_api.schemas.settings import DetectionSettings
from yardlens_api.services.camera_worker import (
    CameraConfig,
    CameraWorker,
    RuntimeState,
    WorkerEvent,
    WorkerOptions,
)
from yardlens_api.services.detector import Detector
from yardlens_api.services.governor import Limits
from yardlens_api.services.loading import SessionState
from yardlens_api.services.plates import PlateReader

logger = logging.getLogger(__name__)

type EventHandler = Callable[[WorkerEvent], None]
type StatusHandler = Callable[[int, CameraStatus, str | None], None]
type SessionHandler = Callable[[SessionState], None]

_DISABLED_STATE = RuntimeState(
    status=CameraStatus.DISABLED,
    last_error=None,
    last_frame_at=None,
    measured_fps=0.0,
    frame_width=None,
    frame_height=None,
)


class CameraManager:
    def __init__(
        self,
        *,
        detector: Detector,
        detection_settings: Callable[[], DetectionSettings],
        options: WorkerOptions,
        loop: asyncio.AbstractEventLoop,
        on_event: EventHandler,
        on_status: StatusHandler,
        on_session: SessionHandler | None = None,
        plate_reader: PlateReader | None = None,
        limits: Callable[[], Limits] | None = None,
        names: dict[int, str] | None = None,
        workers_enabled: bool = True,
    ) -> None:
        self._detector = detector
        self._detection_settings = detection_settings
        self._options = options
        self._loop = loop
        self._on_event = on_event
        self._on_status = on_status
        self._on_session = on_session
        self._plate_reader = plate_reader
        self._limits = limits
        self._workers_enabled = workers_enabled
        self._workers: dict[int, CameraWorker] = {}
        self._names = names if names is not None else {}
        self._retiring: set[CameraWorker] = set()

    # -- WorkerListener (called from worker threads) -------------------------------------------

    def on_event(self, event: WorkerEvent) -> None:
        self._loop.call_soon_threadsafe(self._on_event, event)

    def on_status(self, camera_id: int, status: CameraStatus, last_error: str | None) -> None:
        self._loop.call_soon_threadsafe(self._on_status, camera_id, status, last_error)

    def on_session(self, session: SessionState) -> None:
        if self._on_session is not None:
            self._loop.call_soon_threadsafe(self._on_session, session)

    # -- lifecycle -----------------------------------------------------------------------------

    def camera_name(self, camera_id: int) -> str:
        return self._names.get(camera_id, f"Camera {camera_id}")

    def sync(self, config: CameraConfig, *, enabled: bool) -> None:
        """Bring the worker for ``config`` in line with the desired configuration."""
        self._names[config.id] = config.name
        current = self._workers.get(config.id)
        if not enabled or not self._workers_enabled:
            if current is not None:
                self._retire(config.id)
                self._on_status(config.id, CameraStatus.DISABLED, None)
            return
        if current is not None:
            if not _needs_restart(current.config, config):
                current.update_config(config)
                return
            self._retire(config.id)
        worker = CameraWorker(
            config,
            detector=self._detector,
            detection_settings=self._detection_settings,
            listener=self,
            options=self._options,
            plate_reader=self._plate_reader,
            limits=self._limits,
        )
        self._workers[config.id] = worker
        worker.start()
        logger.info("Camera worker started", extra={"camera_id": config.id, "camera": config.name})

    def remove(self, camera_id: int) -> None:
        self._retire(camera_id)
        self._names.pop(camera_id, None)

    def _retire(self, camera_id: int) -> None:
        worker = self._workers.pop(camera_id, None)
        if worker is None:
            return
        worker.stop()
        self._retiring.add(worker)
        task = self._loop.create_task(asyncio.to_thread(worker.join, 10.0))
        task.add_done_callback(lambda _: self._retiring.discard(worker))

    async def shutdown(self, join_timeout: float = 10.0) -> None:
        workers = [*self._workers.values(), *self._retiring]
        self._workers.clear()
        for worker in workers:
            worker.stop()
        await asyncio.gather(*(asyncio.to_thread(worker.join, join_timeout) for worker in workers))
        logger.info("Camera workers stopped", extra={"count": len(workers)})

    # -- queries -------------------------------------------------------------------------------

    def worker(self, camera_id: int) -> CameraWorker | None:
        return self._workers.get(camera_id)

    def runtime(self, camera_id: int) -> RuntimeState:
        worker = self._workers.get(camera_id)
        return worker.state() if worker is not None else _DISABLED_STATE

    def workers(self) -> list[CameraWorker]:
        return list(self._workers.values())

    def online_count(self) -> int:
        return sum(
            1 for worker in self._workers.values() if worker.state().status == CameraStatus.ONLINE
        )


def _needs_restart(old: CameraConfig, new: CameraConfig) -> bool:
    """Name, counting line, loading zone and plate stream change live; source and rate need a
    new worker."""
    return (old.source_type, old.url, old.inference_fps) != (
        new.source_type,
        new.url,
        new.inference_fps,
    )
