"""The worker agent: stays connected to the server and runs the cameras it is given.

It runs the same camera code as the server (``CameraManager``) on this machine's CPU and GPU.
Reports go through the ``Outbox``, so nothing is lost to a dropped connection. Cut off from the
server for longer than ``WORKER_LEASE_SECONDS`` it stops its cameras, leaving loading sessions
open: by then the server may be handing them to another node, and no camera may run twice.
"""

import asyncio
import contextlib
import hashlib
import logging
import math
import platform
import socket
import ssl
import threading
import time
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import psutil
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from yardlens_api.core.enums import CameraStatus
from yardlens_api.schemas.settings import DetectionSettings, PerformanceSettings
from yardlens_api.schemas.worker_protocol import (
    CLOSE_REPLACED,
    CLOSE_UNAUTHORIZED,
    CLOSE_UNSUPPORTED,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    SERVER_MESSAGES,
    Ack,
    Assign,
    CameraReport,
    EventReport,
    Hello,
    LiveFrame,
    NodeSettings,
    Ping,
    Pong,
    ProtocolError,
    RawFrame,
    RawRequest,
    Resources,
    ServerMessage,
    SettingsUpdate,
    States,
    Status,
    Watch,
    Welcome,
    pack,
    unpack,
)
from yardlens_api.schemas.workers import NodeLoad, WorkerInfo
from yardlens_api.services.camera_manager import CameraManager
from yardlens_api.services.camera_worker import (
    BACKOFF_INITIAL_SECONDS,
    WorkerEvent,
    WorkerOptions,
    next_backoff,
    with_jitter,
)
from yardlens_api.services.cluster import WORKER_LEASE_SECONDS
from yardlens_api.services.detector import Detector, ModelPool, YoloDetector
from yardlens_api.services.governor import LEVEL_NAMES, Budget
from yardlens_api.services.loading import SessionState
from yardlens_api.services.loading_store import InterruptedSession
from yardlens_api.services.plates import FastAlprReader
from yardlens_api.services.resources import (
    ResourceMonitor,
    apply_process_plan,
    container_cpu_limit,
    plan_resources,
)
from yardlens_api.services.workers import certificate_fingerprint
from yardlens_api.worker.config import WorkerSettings
from yardlens_api.worker.outbox import Outbox, Report

logger = logging.getLogger(__name__)

STATES_SECONDS = 1.0
RESOURCES_SECONDS = 2.0
# A watched camera keeps sending this long after the server last asked for it.
WATCH_HOLD_SECONDS = 6.0
FRAME_POLL_SECONDS = 0.02
# Live frames are skipped while this much is waiting to go out: reports come first.
FRAME_BACKLOG = 4
# A refused token or an incompatible server: try again rarely.
REFUSED_RETRY_SECONDS = 300.0
SWEEP_SECONDS = 3600.0
PHOTO_MAX_AGE = timedelta(days=1)


class _RefusedError(Exception):
    """The server will not take this worker as it is; waiting longer will not help soon."""


def _pin(value: str) -> str:
    return value.lower().removeprefix("sha256:").replace(":", "").strip()


class Agent:
    def __init__(
        self,
        settings: WorkerSettings,
        *,
        detector: Detector | None = None,
        lease_seconds: float = WORKER_LEASE_SECONDS,
    ) -> None:
        self._config = settings
        self._lease = lease_seconds
        self._token = settings.worker_token.get_secret_value()
        self._base = settings.server.rstrip("/")
        self._injected = detector
        self._settings = NodeSettings(
            detection=DetectionSettings(model=settings.default_model),
            performance=PerformanceSettings(),
        )
        self._epochs: dict[int, int] = {}
        self._outbox = Outbox(settings.snapshots_dir)
        self._frames_out: asyncio.Queue[str | bytes] | None = None
        self._reports: asyncio.Queue[Report] | None = None
        self._socket: ClientConnection | None = None
        self._watch: dict[int, float] = {}
        self._last_heard = time.monotonic()
        self._stopping = asyncio.Event()
        self._model_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._ssl: ssl.SSLContext | None = None
        self.connected = asyncio.Event()
        self.worker_id: int | None = None

    # -- setup -----------------------------------------------------------------------------------

    def _build(self, loop: asyncio.AbstractEventLoop) -> None:
        config = self._config
        self._cpu_limit = container_cpu_limit()
        logical = psutil.cpu_count(logical=True) or 1
        physical = psutil.cpu_count(logical=False) or logical
        if self._cpu_limit:
            cap = max(1, math.ceil(self._cpu_limit))
            logical, physical = min(logical, cap), min(physical, cap)
        plan = plan_resources(
            inference_threads=config.inference_threads, logical=logical, physical=physical
        )
        apply_process_plan(plan, low_priority=config.low_priority)
        self._plan = plan
        lock = threading.Lock()

        def new_detector(model_name: str) -> YoloDetector:
            return YoloDetector(
                models_dir=config.models_dir,
                model_name=model_name,
                device=config.inference_device,
                backend=config.inference_backend,
                cpu_threads=plan.inference_threads,
                lock=lock,
            )

        def load_model(model_name: str) -> Detector:
            if self._injected is not None:
                return self._injected
            self._fetch_model(model_name)
            loaded = new_detector(model_name)
            loaded.load(model_name)
            return loaded

        self._detector: Detector = self._injected or new_detector(config.default_model)
        self._models = ModelPool(load_model)

        def budget() -> Budget:
            performance = self._settings.performance
            return Budget(
                cpu_limit_percent=performance.cpu_limit_percent,
                adaptive=performance.adaptive_fps,
                idle_fps=performance.idle_fps,
                stream_fps=config.stream_fps,
                jpeg_quality=config.stream_jpeg_quality,
            )

        self._monitor = ResourceMonitor(
            plan=plan,
            budget=budget,
            detector_busy_seconds=lambda: self._detector.busy_seconds + self._models.busy_seconds,
            inference_on_gpu=lambda: self._detector.status.device.startswith(("cuda", "mps")),
            data_dir=config.data_dir,
        )
        self._manager = CameraManager(
            detector=self._detector,
            detection_settings=lambda: self._settings.detection,
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
            loop=loop,
            on_event=self._on_event,
            on_status=self._on_status,
            on_session=self._on_session,
            plate_reader=FastAlprReader(threads=plan.plate_threads)
            if self._injected is None
            else None,
            limits=self._monitor.limits,
            detector_for=self._models.get,
        )

    # -- running ---------------------------------------------------------------------------------

    async def run(self) -> None:
        """Serve the server until ``stop``; reconnects on its own."""
        self._config.ensure_directories()
        self._build(asyncio.get_running_loop())
        self._monitor.start()
        self._spawn(self._watchdog())
        self._spawn(self._sweep())
        try:
            await self._connect_loop()
        finally:
            for task in list(self._tasks):
                task.cancel()
            # Leaves loading sessions open, for this worker or another node to carry on.
            await self._manager.shutdown()
            await asyncio.to_thread(self._monitor.stop)

    def running(self) -> dict[int, int]:
        """Cameras running here, with the epoch each runs under."""
        return {
            worker.config.id: self._epochs.get(worker.config.id, 0)
            for worker in self._manager.workers()
        }

    def stop(self) -> None:
        self._stopping.set()
        if self._socket is not None:
            self._spawn(self._socket.close())

    def _spawn(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _connect_loop(self) -> None:
        delay = BACKOFF_INITIAL_SECONDS
        while not self._stopping.is_set():
            wait = with_jitter(delay)
            try:
                await self._serve()
                delay = BACKOFF_INITIAL_SECONDS
                wait = with_jitter(delay)
            except _RefusedError as exc:
                logger.error(str(exc))
                wait = REFUSED_RETRY_SECONDS
            except InvalidStatus as exc:
                if exc.response.status_code in (401, 403):
                    logger.error(
                        "The server refused this worker's token: it was removed or mistyped. "
                        "Add the worker again in Settings and use the new command."
                    )
                    wait = REFUSED_RETRY_SECONDS
                else:
                    logger.warning(
                        "The server refused the connection",
                        extra={"status": exc.response.status_code},
                    )
            except (OSError, TimeoutError, InvalidHandshake, ConnectionClosed) as exc:
                logger.warning(
                    "Cannot reach the server", extra={"server": self._base, "error": str(exc)}
                )
            except ValueError as exc:  # a certificate that does not match the pin
                logger.error(str(exc))
                wait = REFUSED_RETRY_SECONDS
            delay = next_backoff(delay)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), wait)

    async def _serve(self) -> None:
        """One connection, until it ends."""
        if self._base.startswith("https://") and self._ssl is None:
            self._ssl = await asyncio.to_thread(self._tls_context)
        url = f"{'wss' if self._base.startswith('https://') else 'ws'}://"
        url += f"{self._base.split('://', 1)[1]}/api/workers/connect"
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {self._token}"},
            ssl=self._ssl,
            max_size=MAX_MESSAGE_BYTES,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            user_agent_header=f"yardlens-worker/{version('yardlens-api')}",
        ) as websocket:
            self._socket = websocket
            self._frames_out = asyncio.Queue()
            self._reports = asyncio.Queue()
            await websocket.send(pack(await asyncio.to_thread(self._hello)))
            tasks = [
                asyncio.create_task(self._write(websocket)),
                asyncio.create_task(self._encode_reports()),
                asyncio.create_task(self._tick()),
                asyncio.create_task(self._pump_frames()),
            ]
            for report in self._outbox.pending():
                self._reports.put_nowait(report)
            try:
                async for frame in websocket:
                    self._last_heard = time.monotonic()
                    try:
                        message, _ = unpack(SERVER_MESSAGES, frame)
                    except ProtocolError:
                        logger.warning("Malformed message from the server; reconnecting")
                        return
                    await self._handle(message)
            except ConnectionClosed:
                pass
            finally:
                self.connected.clear()
                self._socket = None
                self._frames_out = None
                self._reports = None
                for task in tasks:
                    task.cancel()
            code, reason = websocket.close_code, websocket.close_reason or ""
        if code == CLOSE_UNAUTHORIZED:
            raise _RefusedError("This worker was removed on the server; add it again in Settings")
        if code == CLOSE_UNSUPPORTED:
            raise _RefusedError(f"This worker does not match the server's version: {reason}")
        if code == CLOSE_REPLACED:
            logger.warning("Another worker connected with the same token")
        elif not self._stopping.is_set():
            logger.warning("Connection to the server lost", extra={"code": code})

    # -- talking to the server -------------------------------------------------------------------

    def _send(self, message: Any, files: tuple[bytes, ...] = ()) -> None:
        if self._frames_out is not None:
            self._frames_out.put_nowait(pack(message, files))

    async def _write(self, websocket: ClientConnection) -> None:
        queue = self._frames_out
        assert queue is not None
        while True:
            await websocket.send(await queue.get())

    async def _encode_reports(self) -> None:
        """Reports in order, each with its photos read from disk just before it goes out."""
        reports, out = self._reports, self._frames_out
        assert reports is not None
        assert out is not None
        while True:
            report = await reports.get()
            if self._outbox.is_pending(report.seq):
                out.put_nowait(await asyncio.to_thread(self._outbox.encode, report))

    def _queue_report(self, report: Report) -> None:
        if self._reports is not None:
            self._reports.put_nowait(report)

    def _hello(self) -> Hello:
        return Hello(
            protocol=PROTOCOL_VERSION,
            info=self._info(),
            running=self.running(),
            time=datetime.now(UTC),
        )

    def _info(self) -> WorkerInfo:
        status = self._detector.status
        backend, device = status.backend, status.device
        if isinstance(self._detector, YoloDetector) and not status.loaded:
            plan = self._detector.resolve_plan()
            backend, device = plan.backend.value, plan.device
        return WorkerInfo(
            hostname=socket.gethostname()[:255],
            platform=platform.platform()[:255],
            version=version("yardlens-api"),
            logical_cpus=self._plan.logical_cpus,
            physical_cores=self._plan.physical_cores,
            cpu_limit=self._cpu_limit,
            memory_total_bytes=int(psutil.virtual_memory().total),
            gpu=self._monitor.gpu.name,
            backend=backend,
            device=device,
        )

    async def _handle(self, message: ServerMessage) -> None:
        match message:
            case Welcome(worker_id=worker_id, name=name, settings=settings):
                self.worker_id = worker_id
                logger.info("Connected to the server", extra={"worker": name})
                self._apply_settings(settings)
                self.connected.set()
            case Assign():
                self._assign(message)
            case SettingsUpdate(settings=settings):
                self._apply_settings(settings)
            case Watch(cameras=cameras):
                until = time.monotonic() + WATCH_HOLD_SECONDS
                self._watch = dict.fromkeys(cameras, until)
            case RawRequest():
                self._spawn(self._send_raw(message))
            case Ack(seqs=seqs):
                await asyncio.to_thread(self._outbox.ack, seqs)
            case Ping(id=ping_id):
                self._send(Pong(id=ping_id, time=datetime.now(UTC)))

    def _apply_settings(self, settings: NodeSettings) -> None:
        self._settings = settings
        self._monitor.refresh()
        status = self._detector.status
        if settings.detection.model != status.model or not status.loaded:
            self._spawn(self._load_general_model())

    async def _load_general_model(self) -> None:
        async with self._model_lock:
            model = self._settings.detection.model
            status = self._detector.status
            if model == status.model and status.loaded:
                return
            try:
                await asyncio.to_thread(self._fetch_model, model)
                await asyncio.to_thread(self._detector.load, model)
            except Exception:
                logger.exception("Could not load the detection model", extra={"model": model})
            else:
                logger.info("Detection model loaded", extra={"model": model})

    def _assign(self, message: Assign) -> None:
        wanted = {item.config.id: item for item in message.cameras}
        stop = set(message.stop)
        for worker in self._manager.workers():
            camera_id = worker.config.id
            if camera_id in wanted:
                continue
            if camera_id in stop:
                self._manager.sync(worker.config, enabled=False)
                logger.info("Camera stopped", extra={"camera_id": camera_id})
            else:
                self._manager.release(camera_id)
                logger.info("Camera handed over", extra={"camera_id": camera_id})
        for camera_id, item in wanted.items():
            self._epochs[camera_id] = item.epoch
            current = self._manager.worker(camera_id)
            if current is not None and current.config == item.config:
                continue
            resume = (
                InterruptedSession(item.resume, item.resume_seen)
                if item.resume is not None and item.resume_seen is not None
                else None
            )
            self._manager.sync(item.config, enabled=True, resume=resume)
        general = self._settings.detection.model
        self._models.retain(self._manager.models_in_use() - {general})

    async def _send_raw(self, request: RawRequest) -> None:
        worker = self._manager.worker(request.camera_id)
        jpeg = await asyncio.to_thread(worker.latest_raw_jpeg) if worker is not None else None
        self._send(RawFrame(request_id=request.request_id), (jpeg,) if jpeg else ())

    async def _tick(self) -> None:
        last_resources = float("-inf")
        while True:
            self._send(States(cameras=self._camera_reports()))
            now = time.monotonic()
            if now - last_resources >= RESOURCES_SECONDS:
                last_resources = now
                load = self._load()
                if load is not None:
                    self._send(Resources(load=load))
            await asyncio.sleep(STATES_SECONDS)

    def _camera_reports(self) -> list[CameraReport]:
        return [
            CameraReport(
                camera_id=worker.config.id,
                epoch=self._epochs.get(worker.config.id, 0),
                state=worker.state(),
            )
            for worker in self._manager.workers()
        ]

    def _load(self) -> NodeLoad | None:
        reading = self._monitor.latest()
        if reading is None:
            return None
        status = self._detector.status
        return NodeLoad(
            cpu_percent=round(reading.cpu_percent, 1),
            process_cpu_percent=round(reading.process_cpu_percent, 1),
            memory_percent=round(reading.memory_percent, 1),
            gpu_percent=reading.gpu_percent,
            detector_busy_percent=round(reading.detector_busy * 100, 1),
            disk_free_bytes=reading.disk_free or None,
            level=LEVEL_NAMES[self._monitor.limits().level],
            model=status.model,
            optimizing=status.optimizing,
        )

    async def _pump_frames(self) -> None:
        """Live frames of the cameras the server watches, each new one once."""
        sent: dict[int, int] = {}
        while True:
            now = time.monotonic()
            self._watch = {camera: until for camera, until in self._watch.items() if until > now}
            out = self._frames_out
            for camera_id in self._watch:
                worker = self._manager.worker(camera_id)
                if worker is None or out is None:
                    continue
                seq, jpeg = worker.latest_jpeg()  # asking keeps it rendering
                if jpeg is None or sent.get(camera_id) == seq or out.qsize() > FRAME_BACKLOG:
                    continue
                sent[camera_id] = seq
                out.put_nowait(pack(LiveFrame(camera_id=camera_id, seq=seq), [jpeg]))
            await asyncio.sleep(FRAME_POLL_SECONDS if self._watch else 0.25)

    # -- camera callbacks (on the event loop) ----------------------------------------------------

    def _on_event(self, event: WorkerEvent) -> None:
        self._queue_report(self._outbox.add_event(event, self._epochs.get(event.camera_id, 0)))

    def _on_session(self, state: SessionState) -> None:
        self._queue_report(self._outbox.add_session(state, self._epochs.get(state.camera_id, 0)))

    def _on_status(self, camera_id: int, status: CameraStatus, error: str | None) -> None:
        epoch = self._epochs.get(camera_id, 0)
        self._send(Status(camera_id=camera_id, epoch=epoch, status=status, last_error=error))

    # -- housekeeping ----------------------------------------------------------------------------

    async def _watchdog(self) -> None:
        """Cut off for longer than the lease: stop the cameras (the server moves them)."""
        while True:
            await asyncio.sleep(min(5.0, self._lease / 2))
            if self.connected.is_set() or not self._manager.workers():
                continue
            if time.monotonic() - self._last_heard > self._lease:
                logger.warning(
                    "No word from the server for a minute; stopping cameras until it is back"
                )
                for worker in self._manager.workers():
                    self._manager.release(worker.config.id)

    async def _sweep(self) -> None:
        """Photos are deleted once delivered; this clears any left behind."""
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            keep = {
                path
                for report in self._outbox.pending()
                for path in _report_photos(report)
                if path is not None
            }
            try:
                removed = await asyncio.to_thread(
                    _remove_old_photos, self._config.snapshots_dir, keep
                )
            except OSError:
                logger.warning("Could not clear old photos", exc_info=True)
            else:
                if removed:
                    logger.info("Cleared old photos", extra={"count": removed})

    # -- files from the server -------------------------------------------------------------------

    def _tls_context(self) -> ssl.SSLContext:
        """Trust the server's own certificate authority, checked against the pin; without a pin,
        the system's. Blocking."""
        pin = self._config.ca_pin
        if not pin:
            return ssl.create_default_context()
        ca_file = self._config.data_dir / "ca.pem"
        pem = ca_file.read_bytes() if ca_file.is_file() else b""
        if not pem or certificate_fingerprint(pem) != _pin(pin):
            # Fetched without verification, then trusted only if it is the pinned certificate.
            response = httpx.get(f"{self._base}/api/workers/ca.pem", verify=False, timeout=15)  # noqa: S501
            response.raise_for_status()
            pem = response.content
            if certificate_fingerprint(pem) != _pin(pin):
                raise ValueError(
                    "The server's certificate authority does not match YARDLENS_CA_PIN; "
                    "copy the command from Settings again"
                )
            ca_file.write_bytes(pem)
        return ssl.create_default_context(cafile=str(ca_file))

    def _fetch_model(self, model: str) -> None:
        """Download a model the server has, unless it is here already. Blocking."""
        target = self._config.models_dir / model
        if target.is_file():
            return
        verify: ssl.SSLContext | bool = self._ssl or True
        url = f"{self._base}/api/workers/models/{quote(model)}"
        headers = {"Authorization": f"Bearer {self._token}"}
        partial = target.with_name(f"{target.name}.part")
        digest = hashlib.sha256()
        with (
            httpx.Client(verify=verify, timeout=httpx.Timeout(30, read=300)) as http,
            http.stream("GET", url, headers=headers) as response,
        ):
            if response.status_code == 404:
                return  # a stock model the server has not downloaded: fetched by Ultralytics
            response.raise_for_status()
            expected = response.headers.get("x-content-sha256", "")
            with partial.open("wb") as file:
                for chunk in response.iter_bytes(1024 * 1024):
                    digest.update(chunk)
                    file.write(chunk)
        if expected and digest.hexdigest() != expected:
            partial.unlink(missing_ok=True)
            raise ValueError(f"Model {model} arrived damaged")
        partial.replace(target)
        logger.info("Model downloaded from the server", extra={"model": model})


def _report_photos(report: Report) -> tuple[str | None, ...]:
    if isinstance(report, EventReport):
        return (report.event.snapshot_path,)
    return (report.session.vehicle_snapshot_path, report.session.plate_snapshot_path)


def _remove_old_photos(root: Path, keep: set[str]) -> int:
    cutoff = time.time() - PHOTO_MAX_AGE.total_seconds()
    removed = 0
    for path in root.rglob("*.jpg"):
        relative = path.relative_to(root).as_posix()
        if relative not in keep and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
