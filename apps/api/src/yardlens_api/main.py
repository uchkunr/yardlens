import argparse
import asyncio
import contextlib
import logging
import multiprocessing
import os
import sys
import threading
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import watchfiles
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from yardlens_api.api.deps import Container
from yardlens_api.api.routes import (
    auth,
    cameras,
    events,
    health,
    loading,
    settings,
    stats,
    streams,
    system,
)
from yardlens_api.api.routes.cameras import camera_config
from yardlens_api.api.security import (
    CsrfMiddleware,
    PrivateStaticFiles,
    allowed_origins,
    require_user,
)
from yardlens_api.core.config import Settings, get_settings
from yardlens_api.core.errors import register_exception_handlers
from yardlens_api.core.i18n import LOGIN_ALERT
from yardlens_api.core.logging import configure_logging
from yardlens_api.core.security import load_secret, password_hasher
from yardlens_api.db.models import Camera
from yardlens_api.db.session import create_engine, create_session_factory, init_models
from yardlens_api.services.auth import AuthService
from yardlens_api.services.camera_manager import CameraManager
from yardlens_api.services.camera_worker import WorkerOptions
from yardlens_api.services.detector import Detector, ModelPool, YoloDetector
from yardlens_api.services.event_bus import EventBus
from yardlens_api.services.event_recorder import EventRecorder
from yardlens_api.services.events import (
    SNAPSHOTS_URL_PREFIX,
    purge_events_before,
    remove_snapshot_files,
)
from yardlens_api.services.governor import Budget
from yardlens_api.services.loading_store import (
    purge_session_images_before,
    take_interrupted_sessions,
)
from yardlens_api.services.notifier import TelegramNotifier
from yardlens_api.services.plates import FastAlprReader
from yardlens_api.services.resources import (
    ResourceMonitor,
    apply_process_plan,
    plan_resources,
)
from yardlens_api.services.settings_store import SettingsStore

logger = logging.getLogger(__name__)

_RETENTION_INTERVAL_SECONDS = 3600
_REVOCATION_INTERVAL_SECONDS = 60
_SOURCE_DIR = Path(__file__).parent


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
        await _ensure_admin(container)
        tasks.spawn(_retention_loop(container), "retention")
        tasks.spawn(_revocation_loop(container), "revocations")
        container.monitor.start()
        container.recorder.start()
        await _start_cameras(container)
        logger.info("YardLens API started", extra={"version": app.version})
        try:
            yield
        finally:
            logger.info("Shutting down")
            await tasks.cancel_all()
            await container.cameras.shutdown()
            await asyncio.to_thread(container.monitor.stop)
            await container.recorder.stop()
            await container.http_client.aclose()
            await container.engine.dispose()

    production = config.environment == "production"
    app = FastAPI(
        title="YardLens API",
        version=version("yardlens-api"),
        description="Camera-based yard monitoring: live detection, tracking and counting.",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )
    origins = allowed_origins(config)
    app.add_middleware(CsrfMiddleware, origins=origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    @app.get("/api/ping", tags=["system"], include_in_schema=False)
    async def ping() -> dict[str, str]:
        """Liveness for container health checks; says nothing else."""
        return {"status": "ok"}

    app.include_router(auth.router, prefix="/api")
    signed_in = [Depends(require_user)]
    for module in (health, system, cameras, streams, events, stats, loading, settings):
        app.include_router(module.router, prefix="/api", dependencies=signed_in)
    app.mount(
        SNAPSHOTS_URL_PREFIX,
        PrivateStaticFiles(directory=config.snapshots_dir, check_dir=False),
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
    http_client = httpx.AsyncClient()
    notifier = TelegramNotifier(http_client)

    async def alert(address: str) -> None:
        telegram = store.telegram()
        if not (telegram.enabled and telegram.bot_token and telegram.chat_id):
            return
        text = LOGIN_ALERT[telegram.language].format(address=address)
        try:
            await notifier.send_message(telegram.bot_token, telegram.chat_id, text)
        except Exception:
            logger.warning("Could not send the sign-in alert", exc_info=True)

    auth_service = AuthService(
        session_factory=session_factory,
        secret=load_secret(config.auth_secret, config.data_dir / "auth-secret"),
        hasher=password_hasher(fast=config.environment == "test"),
        access_ttl=timedelta(minutes=config.access_token_minutes),
        refresh_ttl=timedelta(days=config.refresh_token_days),
        alert=alert,
    )
    await auth_service.load_revocations()

    plan = plan_resources(inference_threads=config.inference_threads)
    apply_process_plan(plan, low_priority=config.low_priority and config.environment != "test")
    logger.info(
        "Resource plan",
        extra={
            "cores": plan.physical_cores,
            "inference_threads": plan.inference_threads,
            "decode_threads": plan.decode_threads,
        },
    )
    # One inference at a time across every model, so the thread budget holds.
    inference_lock = threading.Lock()

    def new_detector(model_name: str) -> YoloDetector:
        return YoloDetector(
            models_dir=config.models_dir,
            model_name=model_name,
            device=config.inference_device,
            backend=config.inference_backend,
            cpu_threads=plan.inference_threads,
            lock=inference_lock,
        )

    def load_model(model_name: str) -> Detector:
        if detector is not None:
            return detector  # an injected (test) detector serves every model
        loaded = new_detector(model_name)
        loaded.load(model_name)
        return loaded

    resolved_detector = detector or new_detector(store.detection().model)
    models = ModelPool(load_model)

    def budget() -> Budget:
        performance = store.performance()
        return Budget(
            cpu_limit_percent=performance.cpu_limit_percent,
            adaptive=performance.adaptive_fps,
            idle_fps=performance.idle_fps,
            stream_fps=config.stream_fps,
            jpeg_quality=config.stream_jpeg_quality,
        )

    monitor = ResourceMonitor(
        plan=plan,
        budget=budget,
        detector_busy_seconds=lambda: resolved_detector.busy_seconds + models.busy_seconds,
        inference_on_gpu=lambda: resolved_detector.status.device.startswith(("cuda", "mps")),
        data_dir=config.data_dir,
    )
    bus = EventBus()
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
            loading_arrive_seconds=config.loading_arrive_seconds,
            loading_depart_seconds=config.loading_depart_seconds,
            hardware_decode=config.hardware_decode,
            decode_threads=plan.decode_threads,
        ),
        loop=asyncio.get_running_loop(),
        on_event=recorder.submit,
        on_status=recorder.publish_status,
        on_session=recorder.submit_session,
        plate_reader=FastAlprReader(threads=plan.plate_threads) if detector is None else None,
        limits=monitor.limits,
        detector_for=models.get,
        names=names,
    )
    return Container(
        settings=config,
        engine=engine,
        session_factory=session_factory,
        detector=resolved_detector,
        models=models,
        settings_store=store,
        cameras=manager,
        bus=bus,
        recorder=recorder,
        notifier=notifier,
        http_client=http_client,
        monitor=monitor,
        auth=auth_service,
        started_at=datetime.now(UTC),
    )


async def _start_cameras(container: Container) -> None:
    """Start every enabled camera, carrying on loading sessions a restart cut off."""
    async with container.session_factory() as session:
        rows = (await session.scalars(select(Camera).order_by(Camera.id))).all()
        running = {camera.id for camera in rows if camera.enabled}
        resumable, closed = await take_interrupted_sessions(session, running_cameras=running)
    if closed:
        logger.info("Closed loading sessions cut off by a restart", extra={"count": closed})
    for camera in rows:
        container.cameras.sync(
            camera_config(camera), enabled=camera.enabled, resume=resumable.get(camera.id)
        )
    general = container.settings_store.detection().model
    container.models.retain(container.cameras.models_in_use() - {general})


async def _load_model(container: Container) -> None:
    model = container.settings_store.detection().model
    try:
        await asyncio.to_thread(container.detector.load, model)
    except Exception:
        logger.exception("Could not load detection model", extra={"model": model})


async def _ensure_admin(container: Container) -> None:
    """Create the administrator from the environment on first start (Docker, tests)."""
    config = container.settings
    if not (config.admin_username and config.admin_password):
        if not await container.auth.has_user():
            logger.warning(
                "No administrator yet: create one with `uv run yardlens-admin create`, or set "
                "YARDLENS_ADMIN_USERNAME and YARDLENS_ADMIN_PASSWORD"
            )
        return
    if not await container.auth.has_user():
        await container.auth.create_admin(config.admin_username, config.admin_password)


async def _retention_loop(container: Container) -> None:
    settings = container.settings
    retention = timedelta(days=settings.event_retention_days)
    image_retention = timedelta(days=settings.session_image_retention_days)
    while True:
        try:
            async with container.session_factory() as session:
                deleted = await purge_events_before(
                    session, datetime.now(UTC) - retention, settings.snapshots_dir
                )
                images = await purge_session_images_before(
                    session, datetime.now(UTC) - image_retention
                )
            await remove_snapshot_files(settings.snapshots_dir, images)
            if deleted:
                logger.info("Purged old events", extra={"count": deleted})
            if images:
                logger.info("Purged old loading session photos", extra={"count": len(images)})
            await container.auth.purge()
        except Exception:
            logger.exception("Retention cleanup failed")
        await asyncio.sleep(_RETENTION_INTERVAL_SECONDS)


async def _revocation_loop(container: Container) -> None:
    """Sessions revoked by another process (``yardlens-admin set-password``) are only in the
    database; pick them up so their access tokens stop working here too."""
    while True:
        await asyncio.sleep(_REVOCATION_INTERVAL_SECONDS)
        try:
            await container.auth.load_revocations()
        except Exception:
            logger.warning("Could not reload revoked sessions", exc_info=True)


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


def _serve(*, reload: bool = False) -> None:
    config = get_settings()
    # The app configures logging itself; this covers uvicorn's own startup and reloader messages.
    configure_logging(config.log_level, json_output=config.log_json)
    uvicorn.run(
        "yardlens_api.main:create_app",
        factory=True,
        host=config.host,
        port=config.port,
        reload=reload,
        reload_dirs=[str(_SOURCE_DIR)] if reload else None,
        log_config=None,
        timeout_graceful_shutdown=10,
        # Behind Caddy: client addresses and https come from its X-Forwarded-* headers.
        proxy_headers=True,
        forwarded_allow_ips=config.forwarded_allow_ips,
    )


def _serve_under_watchfiles() -> None:
    """The server process watchfiles restarts. Windows doesn't end child processes with their
    parent, so when the parent is killed outright (as Nx does when it stops a task), the server
    would keep running on its own; instead it exits as soon as the parent is gone."""
    parent = multiprocessing.parent_process()
    if parent is not None:

        def follow_parent() -> None:
            parent.join()
            os._exit(0)

        threading.Thread(target=follow_parent, name="parent-watch", daemon=True).start()
    _serve()


def main() -> None:
    """Run the API with uvicorn (``uv run yardlens-api``; ``--reload`` for development)."""
    parser = argparse.ArgumentParser(description="Run the YardLens API.")
    parser.add_argument("--reload", action="store_true", help="restart when the source changes")
    args = parser.parse_args()
    if args.reload and sys.platform == "win32":
        # On Windows uvicorn's reloader restarts the server with a Ctrl+C sent to the whole
        # console, which also stops everything else running in it (Nx, Next.js). watchfiles
        # ends only the server process.
        config = get_settings()
        configure_logging(config.log_level, json_output=config.log_json)
        watchfiles.run_process(
            _SOURCE_DIR, target=_serve_under_watchfiles, watch_filter=watchfiles.PythonFilter()
        )
    else:
        _serve(reload=bool(args.reload))
