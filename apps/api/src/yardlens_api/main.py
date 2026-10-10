import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from yardlens_api.api.deps import Container
from yardlens_api.api.routes import cameras, events, health, settings, stats, streams
from yardlens_api.api.routes.cameras import camera_config
from yardlens_api.core.config import Settings, get_settings
from yardlens_api.core.errors import register_exception_handlers
from yardlens_api.core.logging import configure_logging
from yardlens_api.db.models import Camera
from yardlens_api.db.session import create_engine, create_session_factory, init_models
from yardlens_api.services.camera_manager import CameraManager
from yardlens_api.services.camera_worker import WorkerOptions
from yardlens_api.services.detector import Detector, YoloDetector
from yardlens_api.services.event_bus import EventBus
from yardlens_api.services.event_recorder import EventRecorder
from yardlens_api.services.events import SNAPSHOTS_URL_PREFIX, purge_events_before
from yardlens_api.services.notifier import TelegramNotifier
from yardlens_api.services.settings_store import SettingsStore

logger = logging.getLogger(__name__)

_RETENTION_INTERVAL_SECONDS = 3600


def create_app(
    app_settings: Settings | None = None, *, detector: Detector | None = None
) -> FastAPI:
    """Application factory. ``detector`` can be injected (tests use a fake one)."""
    config = app_settings or get_settings()
    configure_logging(config.log_level, json_output=config.log_json)
    config.ensure_directories()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        container = await build_container(config, detector)
        app.state.container = container
        tasks = _TaskGroup()
        if not container.detector.status.loaded:
            tasks.spawn(_load_model(container), "model-loader")
        tasks.spawn(_retention_loop(container), "retention")
        container.recorder.start()
        await _start_workers(container)
        logger.info("YardLens API started", extra={"version": app.version})
        try:
            yield
        finally:
            logger.info("Shutting down")
            await tasks.cancel_all()
            await container.cameras.shutdown()
            await container.recorder.stop()
            await container.http_client.aclose()
            await container.engine.dispose()

    app = FastAPI(
        title="YardLens API",
        version=version("yardlens-api"),
        description="Camera-based yard monitoring: live detection, tracking and counting.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    for module in (health, cameras, streams, events, stats, settings):
        app.include_router(module.router, prefix="/api")
    app.mount(
        SNAPSHOTS_URL_PREFIX,
        StaticFiles(directory=config.snapshots_dir, check_dir=False),
        name="snapshots",
    )
    return app


async def build_container(config: Settings, detector: Detector | None) -> Container:
    engine = create_engine(config.database_url)
    await init_models(engine)
    session_factory = create_session_factory(engine)

    store = SettingsStore(default_model=config.default_model)
    async with session_factory() as session:
        await store.load(session)

    resolved_detector = detector or YoloDetector(
        models_dir=config.models_dir,
        model_name=store.detection().model,
        device=config.inference_device,
    )
    bus = EventBus()
    http_client = httpx.AsyncClient()
    notifier = TelegramNotifier(http_client)
    names: dict[int, str] = {}
    recorder = EventRecorder(
        session_factory=session_factory,
        bus=bus,
        notifier=notifier,
        telegram_settings=store.telegram,
        camera_name=lambda camera_id: names.get(camera_id, f"Camera {camera_id}"),
        snapshots_dir=config.snapshots_dir,
    )
    manager = CameraManager(
        detector=resolved_detector,
        detection_settings=store.detection,
        options=WorkerOptions(
            snapshots_dir=config.snapshots_dir,
            base_dir=config.data_dir,
            jpeg_quality=config.stream_jpeg_quality,
            snapshot_max_width=config.snapshot_max_width,
            max_frame_width=config.max_frame_width,
            stream_fps=config.stream_fps,
        ),
        loop=asyncio.get_running_loop(),
        on_event=recorder.submit,
        on_status=recorder.publish_status,
        names=names,
        workers_enabled=config.workers_enabled,
    )
    return Container(
        settings=config,
        engine=engine,
        session_factory=session_factory,
        detector=resolved_detector,
        settings_store=store,
        cameras=manager,
        bus=bus,
        recorder=recorder,
        notifier=notifier,
        http_client=http_client,
        started_at=datetime.now(UTC),
    )


async def _start_workers(container: Container) -> None:
    async with container.session_factory() as session:
        rows = (await session.scalars(select(Camera).order_by(Camera.id))).all()
    for camera in rows:
        container.cameras.sync(camera_config(camera), enabled=camera.enabled)


async def _load_model(container: Container) -> None:
    model = container.settings_store.detection().model
    try:
        await asyncio.to_thread(container.detector.load, model)
    except Exception:
        logger.exception("Could not load detection model", extra={"model": model})


async def _retention_loop(container: Container) -> None:
    retention = timedelta(days=container.settings.event_retention_days)
    while True:
        try:
            async with container.session_factory() as session:
                deleted = await purge_events_before(
                    session, datetime.now(UTC) - retention, container.settings.snapshots_dir
                )
            if deleted:
                logger.info("Purged old events", extra={"count": deleted})
        except Exception:
            logger.exception("Event retention cleanup failed")
        await asyncio.sleep(_RETENTION_INTERVAL_SECONDS)


class _TaskGroup:
    """Fire-and-forget background tasks that are cancelled together on shutdown."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, None], name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def cancel_all(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task


def main() -> None:
    """Run the API with uvicorn (``uv run yardlens-api``)."""
    config = get_settings()
    uvicorn.run(
        "yardlens_api.main:create_app",
        factory=True,
        host=config.host,
        port=config.port,
        log_config=None,
        timeout_graceful_shutdown=10,
    )
