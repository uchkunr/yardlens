"""Per-camera capture, inference and render worker.

Each worker owns three daemon threads:

* **capture** - connects to the source (with exponential backoff) and takes every frame off the
  stream in real time, but converts and publishes only the frames that inference or a viewer
  is about to use; the rest are dropped undecoded, so slow consumers never build a queue.
* **inference** - takes the latest frame, runs detection, tracking and line counting, and emits
  events with snapshots. Its rate follows ``Pacer``: the configured ``inference_fps`` while
  something moves, a trickle while the scene is still, both scaled by the load governor.
* **render** - at up to ``stream_fps`` draws the latest tracks onto each new frame and publishes
  the annotated JPEG for live viewing, so the feed stays smooth even at a low inference rate.
  It only runs while someone is watching (see ``latest_jpeg``).

Frames are downscaled to ``max_frame_width`` lazily, once per frame that inference or rendering
actually takes, rather than for every decoded frame.

Workers never touch the database or the event loop; they report through ``WorkerListener``.
"""

import logging
import random
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from yardlens_api.core.enums import (
    LOADING_VEHICLE_CLASSES,
    Activity,
    CameraStatus,
    EventType,
    SourceType,
)
from yardlens_api.core.errors import SourceError
from yardlens_api.schemas.camera import CountingLine, LoadingZone
from yardlens_api.schemas.settings import CameraDetection, DetectionSettings, effective_detection
from yardlens_api.services.analyzer import Box, FrameAnalyzer, Observation, TrackedObject, highlight
from yardlens_api.services.detector import Detector
from yardlens_api.services.governor import Limits
from yardlens_api.services.loading import (
    LoadingTracker,
    SessionChange,
    SessionState,
    SessionUpdate,
    largest,
)
from yardlens_api.services.loading_store import InterruptedSession
from yardlens_api.services.pacing import (
    ACTIVE_HOLD_SECONDS,
    Pacer,
    Region,
    analysis_region,
    crop_region,
    input_size,
)
from yardlens_api.services.plate_stream import PlateStream
from yardlens_api.services.plates import PlateReader
from yardlens_api.services.sources import FrameReader, open_reader, resolve_source

logger = logging.getLogger(__name__)

type Frame = NDArray[np.uint8]

BACKOFF_INITIAL_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0
STALL_TIMEOUT_SECONDS = 20.0
_MAX_CONSECUTIVE_EMPTY_READS = 5


@dataclass(frozen=True, slots=True)
class CameraConfig:
    id: int
    name: str
    source_type: SourceType
    url: str
    inference_fps: float
    line: CountingLine | None
    zone: LoadingZone | None = None
    plate_url: str | None = None
    """The camera's high-resolution main stream, opened only to read plates."""
    detection: CameraDetection | None = None
    """Its own detection settings, over the general ones."""


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    camera_id: int
    type: EventType
    occurred_at: datetime
    class_name: str | None = None
    tracker_id: int | None = None
    confidence: float | None = None
    snapshot_path: str | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RuntimeState:
    status: CameraStatus
    last_error: str | None
    last_frame_at: datetime | None
    measured_fps: float
    frame_width: int | None
    frame_height: int | None
    visible: dict[str, int] = field(default_factory=dict[str, int])
    line_in: int = 0
    line_out: int = 0
    loading: SessionState | None = None
    activity: Activity = Activity.ACTIVE
    target_fps: float = 0.0
    hardware_decoding: bool = False
    plate_stream_open: bool = False


class WorkerListener(Protocol):
    """Callbacks invoked from worker threads; implementations must be thread-safe."""

    def on_event(self, event: WorkerEvent) -> None: ...

    def on_status(self, camera_id: int, status: CameraStatus, last_error: str | None) -> None: ...

    def on_session(self, session: SessionState) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkerOptions:
    snapshots_dir: Path
    base_dir: Path
    jpeg_quality: int = 80
    snapshot_max_width: int = 960
    max_frame_width: int = 1280
    stream_fps: float = 15.0
    connect_timeout: float = 15.0
    # Rendering pauses once nobody has asked for a frame for this long.
    viewer_idle_seconds: float = 5.0
    # Loading bays: how long a vehicle stands in the zone before it counts as arrived, and how
    # long the bay stays quiet (no vehicle, no cargo) before it counts as gone.
    loading_arrive_seconds: float = 15.0
    loading_depart_seconds: float = 60.0
    # Inference stays at full rate this long after the last movement.
    active_hold_seconds: float = ACTIVE_HOLD_SECONDS
    # Decode video on the GPU where possible, and cap FFmpeg's threads per stream (0: its default).
    hardware_decode: bool = False
    decode_threads: int = 0


def next_backoff(current: float) -> float:
    return min(current * 2, BACKOFF_MAX_SECONDS)


def with_jitter(delay: float) -> float:
    return delay * random.uniform(0.8, 1.2)  # noqa: S311 - jitter, not cryptography


def downscale(frame: Frame, max_width: int) -> Frame:
    """Shrink frames wider than ``max_width``, keeping the aspect ratio."""
    if frame.shape[1] <= max_width:
        return frame
    height = round(frame.shape[0] * max_width / frame.shape[1])
    return cast("Frame", cv2.resize(frame, (max_width, height), interpolation=cv2.INTER_AREA))


def encode_jpeg(frame: Frame, quality: int, max_width: int | None = None) -> bytes:
    if max_width is not None:
        frame = downscale(frame, max_width)
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buffer.tobytes()


class CameraWorker:
    def __init__(
        self,
        config: CameraConfig,
        *,
        detector: Detector,
        detection_settings: Callable[[], DetectionSettings],
        listener: WorkerListener,
        options: WorkerOptions,
        plate_reader: PlateReader | None = None,
        limits: Callable[[], Limits] | None = None,
        resume: InterruptedSession | None = None,
        detector_for: Callable[[str], Detector] | None = None,
    ) -> None:
        self.config = config
        self._detector = detector
        # Cameras with a model of their own take its detector from here (a shared pool).
        self._detector_for = detector_for
        self._detection_settings = detection_settings
        self._listener = listener
        self._options = options
        self._plate_reader = plate_reader
        fixed = Limits(stream_fps=options.stream_fps, jpeg_quality=options.jpeg_quality)
        self._limits: Callable[[], Limits] = limits or (lambda: fixed)

        self._stop = threading.Event()
        self._frame_ready = threading.Condition()
        self._latest_frame: Frame | None = None
        self._latest_frame_time = 0.0
        self._frame_seq = 0
        self._last_frame_at: datetime | None = None
        self._last_frame_monotonic = 0.0
        # Capture converts a frame only when one of these is due (see ``_frame_wanted``).
        self._next_inference_at = 0.0
        self._inference_seq = 0
        self._last_published = float("-inf")
        # Time between frames of the source, smoothed; it sets the live view's tolerance.
        self._frame_interval = 1.0 / 25
        self._last_grab: float | None = None
        self._hardware_decode = options.hardware_decode
        self._hardware_decoding = False

        self._scale_lock = threading.Lock()
        self._scaled_seq = 0
        self._scaled_frame: Frame | None = None

        self._output_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._jpeg_seq = 0
        self._last_viewed = float("-inf")

        self._state_lock = threading.Lock()
        self._status = CameraStatus.CONNECTING
        self._last_error: str | None = None
        self._measured_fps = 0.0
        self._frame_size: tuple[int, int] | None = None
        self._visible: dict[str, int] = {}

        self._analyzer = FrameAnalyzer(fps=config.inference_fps, line=config.line, zone=config.zone)
        self._loading = self._new_tracker(config.zone)
        if self._loading is not None and resume is not None:
            self._loading.resume(resume.state, t=time.monotonic(), last_seen=resume.last_seen)
            logger.info(
                "Resumed a loading session cut off by a restart",
                extra={"camera_id": config.id, "session_id": resume.state.id},
            )
        self._pacer = Pacer(config.inference_fps, hold_seconds=options.active_hold_seconds)
        self._region: Region | None = analysis_region(config.line, config.zone)
        self._plate_stream = self._new_plate_stream(config)
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name=f"capture-{config.id}", daemon=True
        )
        self._inference_thread = threading.Thread(
            target=self._inference_loop, name=f"inference-{config.id}", daemon=True
        )
        self._render_thread = threading.Thread(
            target=self._render_loop, name=f"render-{config.id}", daemon=True
        )

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        self._capture_thread.start()
        self._inference_thread.start()
        self._render_thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._frame_ready:
            self._frame_ready.notify_all()

    def join(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for thread in (self._render_thread, self._inference_thread, self._capture_thread):
            if thread.is_alive():
                thread.join(max(0.0, deadline - time.monotonic()))
        if self._plate_stream is not None:
            self._plate_stream.stop()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # -- public state ------------------------------------------------------------------------

    def state(self) -> RuntimeState:
        now = time.monotonic()
        limits = self._limits()
        with self._state_lock:
            width, height = self._frame_size or (None, None)
            return RuntimeState(
                activity=self._pacer.activity(now) if limits.adaptive else Activity.ACTIVE,
                target_fps=round(self._pacer.fps(now, limits), 2),
                hardware_decoding=self._hardware_decoding,
                plate_stream_open=bool(self._plate_stream and self._plate_stream.connected),
                status=self._status,
                last_error=self._last_error,
                last_frame_at=self._last_frame_at,
                measured_fps=round(self._measured_fps, 2),
                frame_width=width,
                frame_height=height,
                visible=dict(self._visible),
                line_in=self._analyzer.line_in,
                line_out=self._analyzer.line_out,
                loading=self._loading.session if self._loading else None,
            )

    def latest_jpeg(self) -> tuple[int, bytes | None]:
        """The newest annotated JPEG. Asking for it keeps the render thread working; while nobody
        asks, it pauses and this returns ``None`` until the next frame is rendered."""
        with self._output_lock:
            self._last_viewed = time.monotonic()
            return self._jpeg_seq, self._latest_jpeg

    def latest_raw_jpeg(self) -> bytes | None:
        with self._frame_ready:
            frame = self._latest_frame
            seq = self._frame_seq
        if frame is None:
            return None
        return encode_jpeg(self._scaled(seq, frame), self._options.jpeg_quality)

    def _scaled(self, seq: int, frame: Frame) -> Frame:
        """``frame`` (captured as number ``seq``) at ``max_frame_width``, computed at most once."""
        with self._scale_lock:
            if self._scaled_seq != seq or self._scaled_frame is None:
                self._scaled_frame = downscale(frame, self._options.max_frame_width)
                self._scaled_seq = seq
            return self._scaled_frame

    def update_config(self, config: CameraConfig) -> None:
        """Apply changes that do not require reconnecting (name, counting line, loading zone,
        plate stream)."""
        closed: SessionUpdate | None = None
        retired: PlateStream | None = None
        with self._state_lock:
            self._analyzer.set_line(config.line)
            self._analyzer.zone = config.zone
            self._region = analysis_region(config.line, config.zone)
            tracker = self._loading
            if config.zone is None:
                closed = tracker.close(datetime.now(UTC), time.monotonic()) if tracker else None
                self._loading = None
            elif tracker is None:
                self._loading = self._new_tracker(config.zone)
            else:
                tracker.zone = config.zone
            if config.plate_url != self.config.plate_url:
                retired = self._plate_stream
                self._plate_stream = self._new_plate_stream(config)
        self.config = config
        if retired is not None:
            retired.stop()
        if closed is not None and tracker is not None:
            self._report_session(closed, tracker)

    def _new_plate_stream(self, config: CameraConfig) -> PlateStream | None:
        """Idle until a loading session asks for it, so creating it costs nothing."""
        if not config.plate_url:
            return None
        return PlateStream(
            config.plate_url,
            timeout=self._options.connect_timeout,
            hardware_decode=self._options.hardware_decode,
            decode_threads=self._options.decode_threads,
        )

    def _new_tracker(self, zone: LoadingZone | None) -> LoadingTracker | None:
        if zone is None:
            return None
        return LoadingTracker(
            self.config.id,
            zone,
            arrive_after=self._options.loading_arrive_seconds,
            depart_after=self._options.loading_depart_seconds,
        )

    # -- status ------------------------------------------------------------------------------

    def _set_status(self, status: CameraStatus, error: str | None = None) -> None:
        if self._stop.is_set():
            return
        with self._state_lock:
            if self._status == status and self._last_error == error:
                return
            previous = self._status
            self._status = status
            self._last_error = error
            went_online = status == CameraStatus.ONLINE and previous != CameraStatus.ONLINE
            went_offline = previous == CameraStatus.ONLINE and status in {
                CameraStatus.OFFLINE,
                CameraStatus.ERROR,
            }
        if went_online or went_offline:
            logger.info(
                "Camera status changed",
                extra={"camera_id": self.config.id, "status": status.value, "error": error},
            )
        self._listener.on_status(self.config.id, status, error)
        now = datetime.now(UTC)
        if went_online:
            self._listener.on_event(
                WorkerEvent(camera_id=self.config.id, type=EventType.CAMERA_ONLINE, occurred_at=now)
            )
        elif went_offline:
            self._listener.on_event(
                WorkerEvent(
                    camera_id=self.config.id,
                    type=EventType.CAMERA_OFFLINE,
                    occurred_at=now,
                    details={"reason": error} if error else None,
                )
            )

    # -- capture -----------------------------------------------------------------------------

    def _capture_loop(self) -> None:
        backoff = BACKOFF_INITIAL_SECONDS
        while not self._stop.is_set():
            self._set_status(CameraStatus.CONNECTING, self._last_error_snapshot())
            try:
                reader = self._connect()
            except SourceError as exc:
                self._set_status(CameraStatus.ERROR, str(exc))
                self._stop.wait(with_jitter(backoff))
                backoff = next_backoff(backoff)
                continue
            except Exception as exc:
                logger.exception(
                    "Unexpected error opening source", extra={"camera_id": self.config.id}
                )
                self._set_status(CameraStatus.ERROR, f"Unexpected error: {exc}")
                self._stop.wait(with_jitter(backoff))
                backoff = next_backoff(backoff)
                continue

            try:
                received = self._pump_frames(reader)
            except Exception as exc:
                logger.warning(
                    "Capture failed", extra={"camera_id": self.config.id, "error": str(exc)}
                )
                received = False
            finally:
                reader.close()

            if self._stop.is_set():
                break
            if not received and reader.hardware_decoding:
                # Opened on the GPU but never produced a frame: decode on the CPU from now on.
                logger.warning(
                    "Hardware decoding failed; using the CPU",
                    extra={"camera_id": self.config.id},
                )
                self._hardware_decode = False
            if received:
                backoff = BACKOFF_INITIAL_SECONDS
            self._set_status(CameraStatus.OFFLINE, "Stream ended or stalled; reconnecting")
            self._stop.wait(with_jitter(backoff))
            backoff = next_backoff(backoff)

    def _last_error_snapshot(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def _connect(self) -> FrameReader:
        source = resolve_source(
            self.config.source_type, self.config.url, base_dir=self._options.base_dir
        )
        reader = open_reader(
            source,
            timeout=self._options.connect_timeout,
            snapshot_interval=max(1.0 / self.config.inference_fps, 0.5),
            stop_event=self._stop,
            hardware_decode=self._hardware_decode,
            decode_threads=self._options.decode_threads,
        )
        self._hardware_decoding = reader.hardware_decoding
        return reader

    def _frame_wanted(self, now: float) -> bool:
        """Whether the frame just grabbed is worth converting: inference is due and has not been
        handed a frame yet, or someone is watching and the live view is due a new one."""
        if self._latest_frame is None:
            return True
        if now >= self._next_inference_at and self._frame_seq == self._inference_seq:
            return True
        if now - self._last_viewed <= self._options.viewer_idle_seconds:
            return now - self._last_published >= self._display_gap()
        return False

    def _display_gap(self) -> float:
        """Shortest gap between live-view frames. Half a source frame short of the frame rate's
        interval: otherwise a 30 fps source asked for 15 fps would deliver every frame that
        arrives a hair too early one frame late, which is 10 fps and uneven."""
        return max(0.0, 1.0 / self._limits().stream_fps - self._frame_interval / 2)

    def _pump_frames(self, reader: FrameReader) -> bool:
        """Read until the stream ends or stalls. Returns whether any frame was received."""
        received = False
        empty_reads = 0
        self._last_grab = None
        while not self._stop.is_set():
            grabbed = reader.grab()
            now = time.monotonic()
            if grabbed:
                if self._last_grab is not None and 0 < now - self._last_grab < 1.0:
                    self._frame_interval += 0.1 * (now - self._last_grab - self._frame_interval)
                self._last_grab = now
            frame = reader.retrieve() if grabbed and self._frame_wanted(now) else None
            if not grabbed or (frame is None and self._latest_frame is None):
                empty_reads += 1
                stalled = received and now - self._last_frame_monotonic > STALL_TIMEOUT_SECONDS
                if empty_reads >= _MAX_CONSECUTIVE_EMPTY_READS or stalled:
                    return received
                continue
            empty_reads = 0
            received = True
            self._last_frame_monotonic = now
            self._last_frame_at = datetime.now(UTC)
            if self._status != CameraStatus.ONLINE:
                self._set_status(CameraStatus.ONLINE)
            if frame is None:
                continue  # taken off the stream, nobody needs it converted
            source_size = (int(frame.shape[1]), int(frame.shape[0]))
            # Kept at full size; consumers take the reduced frame through ``_scaled``.
            with self._frame_ready:
                self._latest_frame = frame
                self._latest_frame_time = now
                self._frame_seq += 1
                self._last_published = now
                self._frame_ready.notify_all()
            with self._state_lock:
                self._frame_size = source_size
        return received

    # -- inference ---------------------------------------------------------------------------

    def _inference_loop(self) -> None:
        last_seq = 0
        next_tick = time.monotonic()
        processed_times: list[float] = []
        while not self._stop.is_set():
            # Tells capture when to convert the next frame for us.
            self._next_inference_at = next_tick
            self._stop.wait(max(0.0, next_tick - time.monotonic()))
            with self._frame_ready:
                while self._frame_seq == last_seq and not self._stop.is_set():
                    self._frame_ready.wait(timeout=1.0)
                    if self._latest_frame is not None and self._is_stale():
                        self._clear_visible()
                frame = self._latest_frame
                frame_time = self._latest_frame_time
                last_seq = self._inference_seq = self._frame_seq
            if self._stop.is_set():
                break
            if frame is None:
                continue

            try:
                self._process(self._scaled(last_seq, frame), frame_time, raw=frame)
            except Exception:
                logger.exception("Inference failed", extra={"camera_id": self.config.id})

            now = time.monotonic()
            processed_times = [t for t in processed_times if now - t < 10.0] + [now]
            with self._state_lock:
                self._measured_fps = (
                    (len(processed_times) - 1) / (processed_times[-1] - processed_times[0])
                    if len(processed_times) > 1
                    else 0.0
                )
                fps = self._pacer.fps(now, self._limits())
            next_tick = max(next_tick + 1.0 / fps, now)
        self._finish_loading()

    def _is_stale(self) -> bool:
        return time.monotonic() - self._last_frame_monotonic > STALL_TIMEOUT_SECONDS

    def _clear_visible(self) -> None:
        with self._state_lock:
            self._visible = {}

    def settings(self) -> DetectionSettings:
        """The detection settings this camera uses: its own over the general ones."""
        return effective_detection(self._detection_settings(), self.config.detection)

    def _process(self, frame: Frame, frame_time: float, raw: Frame | None = None) -> None:
        settings = self.settings()
        detector = self._detector
        if self._detector_for is not None and settings.model != detector.status.model:
            detector = self._detector_for(settings.model)
        region = self._region
        crop, (left, top) = crop_region(frame, region)
        detections = detector.detect(
            crop,
            confidence=settings.confidence,
            classes=settings.classes,
            imgsz=input_size(crop.shape[1], crop.shape[0]) if region is not None else None,
        )
        if (left or top) and len(detections) > 0:
            offset = np.array([left, top, left, top], dtype=detections.xyxy.dtype)
            detections.xyxy = detections.xyxy + offset
        with self._state_lock:
            observation = self._analyzer.process(frame, detections, frame_time)
            self._visible = observation.visible
            tracker = self._loading
            # While a vehicle is being loaded the camera stays at full rate, so the first sack
            # after a pause is not missed; standby covers the wait before a session opens.
            self._pacer.observe(
                frame_time,
                moving=observation.moving or (tracker is not None and tracker.session_open),
                standby=tracker is not None and tracker.occupied,
            )
        cargo = frozenset(settings.cargo_classes) if tracker is not None else frozenset[str]()
        if tracker is not None:
            self._update_loading(
                tracker, observation, cargo, frame, frame_time, frame if raw is None else raw
            )

        # On a loading bay, cargo is tallied per vehicle rather than reported item by item.
        batches: tuple[tuple[EventType, list[TrackedObject]], ...] = tuple(
            (event_type, [obj for obj in objects if obj.class_name not in cargo])
            for event_type, objects in (
                (EventType.OBJECT_ENTERED, observation.entered),
                (EventType.LINE_CROSSED_IN, observation.crossed_in),
                (EventType.LINE_CROSSED_OUT, observation.crossed_out),
            )
        )
        if not any(objects for _, objects in batches):
            return
        with self._state_lock:
            # Snapshots show this exact inference frame, so no extrapolation is involved.
            annotated = self._analyzer.render(frame, frame_time)

        occurred_at = datetime.now(UTC)
        for event_type, objects in batches:
            if self._stop.is_set():
                return
            for obj in objects:
                snapshot = self._save_snapshot(highlight(annotated, obj.box), occurred_at)
                self._listener.on_event(
                    WorkerEvent(
                        camera_id=self.config.id,
                        type=event_type,
                        occurred_at=occurred_at,
                        class_name=obj.class_name,
                        tracker_id=obj.tracker_id,
                        confidence=round(obj.confidence, 4),
                        snapshot_path=snapshot,
                        details={"box": [round(value, 1) for value in obj.box]},
                    )
                )

    # -- loading bay -------------------------------------------------------------------------

    def _update_loading(
        self,
        tracker: LoadingTracker,
        observation: Observation,
        cargo: frozenset[str],
        frame: Frame,
        frame_time: float,
        raw: Frame,
    ) -> None:
        height, width = frame.shape[:2]
        vehicles = [obj for obj in observation.objects if obj.class_name in LOADING_VEHICLE_CLASSES]
        loaded = sum(1 for obj in observation.crossed_in if obj.class_name in cargo)
        unloaded = sum(1 for obj in observation.crossed_out if obj.class_name in cargo)
        now = datetime.now(UTC) - timedelta(seconds=max(0.0, time.monotonic() - frame_time))
        with self._state_lock:
            parked = tracker.parked(vehicles, width, height)
            update = tracker.update(
                t=frame_time, now=now, parked=parked, loaded=loaded, unloaded=unloaded
            )
            read_plate = self._plate_reader is not None and tracker.wants_plate(frame_time)
            plate_pending = self._plate_reader is not None and tracker.plate_pending
            plate_stream = self._plate_stream
        if plate_pending and plate_stream is not None:
            plate_stream.want()
        vehicle = largest(parked)
        if update is not None:
            self._report_session(update, tracker, frame=frame, vehicle=vehicle)
        if read_plate and vehicle is not None:
            source = plate_stream.latest() if plate_stream is not None else None
            self._read_plate(
                tracker, vehicle.box, frame, raw if source is None else source, frame_time
            )

    def _read_plate(
        self, tracker: LoadingTracker, box: Box, frame: Frame, source: Frame, frame_time: float
    ) -> None:
        """Read the plate inside ``box`` (in ``frame`` coordinates) from ``source``: the same
        picture at full resolution, from this stream or the camera's main stream."""
        reader = self._plate_reader
        if reader is None:
            return
        scale = (source.shape[1] / frame.shape[1], source.shape[0] / frame.shape[0])
        try:
            reads = reader.read(crop_box(source, box, scale=scale, margin=0.1))
        except Exception:
            logger.exception("Plate reading failed", extra={"camera_id": self.config.id})
            reads = []
        with self._state_lock:
            update = tracker.add_plate_reads(reads, frame_time)
            crop = tracker.plate_crop if update is not None else None
        if update is None:
            return
        session = update.session
        if crop is not None:
            path = self._write_image(_session_image(session, "plate"), crop, resize=False)
            if path is not None:
                with self._state_lock:
                    tracker.set_snapshots(plate=path)
                session.plate_snapshot_path = path
        self._listener.on_session(session)

    def _report_session(
        self,
        update: SessionUpdate,
        tracker: LoadingTracker,
        *,
        frame: Frame | None = None,
        vehicle: TrackedObject | None = None,
    ) -> None:
        session = update.session
        if update.change is SessionChange.OPENED and frame is not None:
            image = highlight(frame, vehicle.box) if vehicle is not None else frame
            vehicle_path = self._write_image(_session_image(session, "vehicle"), image)
            with self._state_lock:
                crop = tracker.plate_crop
            plate_path = (
                self._write_image(_session_image(session, "plate"), crop, resize=False)
                if crop is not None
                else None
            )
            with self._state_lock:
                tracker.set_snapshots(vehicle=vehicle_path, plate=plate_path)
            session.vehicle_snapshot_path = vehicle_path
            session.plate_snapshot_path = plate_path or session.plate_snapshot_path
        self._listener.on_session(session)

        if update.change is SessionChange.OPENED:
            self._listener.on_event(
                WorkerEvent(
                    camera_id=self.config.id,
                    type=EventType.VEHICLE_ARRIVED,
                    occurred_at=session.arrived_at,
                    class_name=session.vehicle_class,
                    snapshot_path=self._copy_for_event(session.vehicle_snapshot_path),
                    details={"session_id": session.id, "plate": session.plate},
                )
            )
        elif update.change is SessionChange.CLOSED:
            departed_at = session.departed_at or datetime.now(UTC)
            self._listener.on_event(
                WorkerEvent(
                    camera_id=self.config.id,
                    type=EventType.LOADING_COMPLETED,
                    occurred_at=departed_at,
                    class_name=session.vehicle_class,
                    snapshot_path=self._copy_for_event(session.vehicle_snapshot_path),
                    details={
                        "session_id": session.id,
                        "plate": session.plate,
                        "loaded": session.loaded,
                        "unloaded": session.unloaded,
                        "duration_seconds": round(
                            (departed_at - session.arrived_at).total_seconds()
                        ),
                    },
                )
            )

    def _finish_loading(self) -> None:
        """Close the open session when the worker stops, so it is not left hanging."""
        with self._state_lock:
            tracker = self._loading
            closed = tracker.close(datetime.now(UTC), time.monotonic()) if tracker else None
        if closed is not None and tracker is not None:
            self._report_session(closed, tracker)

    # -- render ------------------------------------------------------------------------------

    def _render_loop(self) -> None:
        last_seq = 0
        while not self._stop.is_set():
            with self._frame_ready:
                while self._frame_seq == last_seq and not self._stop.is_set():
                    self._frame_ready.wait(timeout=1.0)
                frame = self._latest_frame
                frame_time = self._latest_frame_time
                last_seq = self._frame_seq
            started = time.monotonic()
            limits = self._limits()
            if self._stop.is_set():
                break
            if frame is None:
                continue
            with self._output_lock:
                idle = time.monotonic() - self._last_viewed > self._options.viewer_idle_seconds
                if idle:
                    # Dropped so a returning viewer gets a fresh frame, never a stale one.
                    self._latest_jpeg = None
            if idle:
                continue
            try:
                scaled = self._scaled(last_seq, frame)
                with self._state_lock:
                    annotated = self._analyzer.render(scaled, frame_time)
                jpeg = encode_jpeg(annotated, limits.jpeg_quality)
            except Exception:
                logger.exception("Rendering failed", extra={"camera_id": self.config.id})
            else:
                with self._output_lock:
                    self._latest_jpeg = jpeg
                    self._jpeg_seq += 1
            # Never longer than capture's own spacing, so no frame it converts for us is skipped.
            self._stop.wait(max(0.0, self._display_gap() - (time.monotonic() - started)))

    def _save_snapshot(self, frame: Frame, occurred_at: datetime) -> str | None:
        relative = Path(
            occurred_at.strftime("%Y/%m/%d"),
            f"cam{self.config.id}-{occurred_at.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}.jpg",
        )
        return self._write_image(relative, frame)

    def _write_image(self, relative: Path, frame: Frame, *, resize: bool = True) -> str | None:
        """Save a JPEG under the snapshots directory; returns its path relative to it, or
        ``None`` when it could not be saved or the disk is nearly full."""
        if not self._limits().save_images:
            return None
        width = self._options.snapshot_max_width if resize else None
        target = self._options.snapshots_dir / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encode_jpeg(frame, quality=75, max_width=width))
        except OSError, ValueError:
            logger.exception("Could not save snapshot", extra={"camera_id": self.config.id})
            return None
        return relative.as_posix()

    def _copy_for_event(self, session_path: str | None) -> str | None:
        """Events get their own copy: event retention deletes it, the session keeps the original."""
        if session_path is None or not self._limits().save_images:
            return None
        now = datetime.now(UTC)
        relative = Path(
            now.strftime("%Y/%m/%d"),
            f"cam{self.config.id}-{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}.jpg",
        )
        target = self._options.snapshots_dir / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self._options.snapshots_dir / session_path, target)
        except OSError:
            logger.exception("Could not copy snapshot", extra={"camera_id": self.config.id})
            return None
        return relative.as_posix()


def _session_image(session: SessionState, kind: str) -> Path:
    """One file per session and kind, overwritten as a better picture comes along."""
    return Path("sessions", session.arrived_at.strftime("%Y/%m/%d"), f"{session.id}-{kind}.jpg")


def crop_box(image: Frame, box: Box, *, scale: tuple[float, float], margin: float) -> Frame:
    """The part of ``image`` under ``box`` (given at 1/``scale`` resolution, horizontally and
    vertically: a camera's two streams may differ in aspect), plus a margin."""
    x1, x2 = box[0] * scale[0], box[2] * scale[0]
    y1, y2 = box[1] * scale[1], box[3] * scale[1]
    pad_x, pad_y = (x2 - x1) * margin, (y2 - y1) * margin
    height, width = image.shape[:2]
    left, top = max(0, int(x1 - pad_x)), max(0, int(y1 - pad_y))
    right, bottom = min(width, int(x2 + pad_x)), min(height, int(y2 + pad_y))
    return image[top:bottom, left:right]
