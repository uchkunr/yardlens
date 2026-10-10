"""Per-camera capture, inference and render worker.

Each worker owns three daemon threads:

* **capture** — connects to the source (with exponential backoff), reads frames in real time and
  keeps only the most recent one, so slow consumers never build a queue.
* **inference** — at ``inference_fps`` takes the latest frame, runs detection, tracking and line
  counting, and emits events with snapshots.
* **render** — at up to ``stream_fps`` draws the latest tracks onto each new frame and publishes
  the annotated JPEG for live viewing, so the feed stays smooth even at a low inference rate.

Workers never touch the database or the event loop; they report through ``WorkerListener``.
"""

import logging
import random
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from yardlens_api.core.enums import CameraStatus, EventType, SourceType
from yardlens_api.core.errors import SourceError
from yardlens_api.schemas.camera import CountingLine
from yardlens_api.schemas.settings import DetectionSettings
from yardlens_api.services.analyzer import FrameAnalyzer, TrackedObject, highlight
from yardlens_api.services.detector import Detector
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


class WorkerListener(Protocol):
    """Callbacks invoked from worker threads; implementations must be thread-safe."""

    def on_event(self, event: WorkerEvent) -> None: ...

    def on_status(self, camera_id: int, status: CameraStatus, last_error: str | None) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkerOptions:
    snapshots_dir: Path
    base_dir: Path
    jpeg_quality: int = 80
    snapshot_max_width: int = 960
    max_frame_width: int = 1280
    stream_fps: float = 15.0
    connect_timeout: float = 15.0


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
    ) -> None:
        self.config = config
        self._detector = detector
        self._detection_settings = detection_settings
        self._listener = listener
        self._options = options

        self._stop = threading.Event()
        self._frame_ready = threading.Condition()
        self._latest_frame: Frame | None = None
        self._latest_frame_time = 0.0
        self._frame_seq = 0
        self._last_frame_at: datetime | None = None
        self._last_frame_monotonic = 0.0

        self._output_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._jpeg_seq = 0

        self._state_lock = threading.Lock()
        self._status = CameraStatus.CONNECTING
        self._last_error: str | None = None
        self._measured_fps = 0.0
        self._frame_size: tuple[int, int] | None = None
        self._visible: dict[str, int] = {}

        self._analyzer = FrameAnalyzer(fps=config.inference_fps, line=config.line)
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

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # -- public state ------------------------------------------------------------------------

    def state(self) -> RuntimeState:
        with self._state_lock:
            width, height = self._frame_size or (None, None)
            return RuntimeState(
                status=self._status,
                last_error=self._last_error,
                last_frame_at=self._last_frame_at,
                measured_fps=round(self._measured_fps, 2),
                frame_width=width,
                frame_height=height,
                visible=dict(self._visible),
                line_in=self._analyzer.line_in,
                line_out=self._analyzer.line_out,
            )

    def latest_jpeg(self) -> tuple[int, bytes | None]:
        with self._output_lock:
            return self._jpeg_seq, self._latest_jpeg

    def latest_raw_jpeg(self) -> bytes | None:
        with self._frame_ready:
            frame = self._latest_frame
        if frame is None:
            return None
        return encode_jpeg(frame, self._options.jpeg_quality)

    def update_config(self, config: CameraConfig) -> None:
        """Apply changes that do not require reconnecting (name, counting line)."""
        with self._state_lock:
            self._analyzer.set_line(config.line)
        self.config = config

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
        return open_reader(
            source,
            timeout=self._options.connect_timeout,
            snapshot_interval=max(1.0 / self.config.inference_fps, 0.5),
            stop_event=self._stop,
        )

    def _pump_frames(self, reader: FrameReader) -> bool:
        """Read until the stream ends or stalls. Returns whether any frame was received."""
        received = False
        empty_reads = 0
        while not self._stop.is_set():
            frame = reader.read()
            now = time.monotonic()
            if frame is None:
                empty_reads += 1
                stalled = received and now - self._last_frame_monotonic > STALL_TIMEOUT_SECONDS
                if empty_reads >= _MAX_CONSECUTIVE_EMPTY_READS or stalled:
                    return received
                continue
            empty_reads = 0
            received = True
            source_size = (int(frame.shape[1]), int(frame.shape[0]))
            # Everything downstream (inference, rendering, snapshots) uses the reduced frame.
            frame = downscale(frame, self._options.max_frame_width)
            with self._frame_ready:
                self._latest_frame = frame
                self._latest_frame_time = now
                self._frame_seq += 1
                self._last_frame_at = datetime.now(UTC)
                self._last_frame_monotonic = now
                self._frame_ready.notify_all()
            with self._state_lock:
                self._frame_size = source_size
            if self._status != CameraStatus.ONLINE:
                self._set_status(CameraStatus.ONLINE)
        return received

    # -- inference ---------------------------------------------------------------------------

    def _inference_loop(self) -> None:
        interval = 1.0 / self.config.inference_fps
        last_seq = 0
        next_tick = time.monotonic()
        processed_times: list[float] = []
        while not self._stop.is_set():
            with self._frame_ready:
                while self._frame_seq == last_seq and not self._stop.is_set():
                    self._frame_ready.wait(timeout=1.0)
                    if self._latest_frame is not None and self._is_stale():
                        self._clear_visible()
                frame = self._latest_frame
                frame_time = self._latest_frame_time
                last_seq = self._frame_seq
            if self._stop.is_set():
                break
            if frame is None:
                continue

            try:
                self._process(frame, frame_time)
            except Exception:
                logger.exception("Inference failed", extra={"camera_id": self.config.id})

            now = time.monotonic()
            processed_times = [t for t in processed_times if now - t < 5.0] + [now]
            with self._state_lock:
                self._measured_fps = (
                    (len(processed_times) - 1) / (processed_times[-1] - processed_times[0])
                    if len(processed_times) > 1
                    else 0.0
                )
            next_tick = max(next_tick + interval, now)
            self._stop.wait(max(0.0, next_tick - time.monotonic()))

    def _is_stale(self) -> bool:
        return time.monotonic() - self._last_frame_monotonic > STALL_TIMEOUT_SECONDS

    def _clear_visible(self) -> None:
        with self._state_lock:
            self._visible = {}

    def _process(self, frame: Frame, frame_time: float) -> None:
        settings = self._detection_settings()
        detections = self._detector.detect(
            frame, confidence=settings.confidence, classes=settings.classes
        )
        with self._state_lock:
            observation = self._analyzer.process(frame, detections, frame_time)
            self._visible = observation.visible
            has_events = bool(
                observation.entered or observation.crossed_in or observation.crossed_out
            )
            # Snapshots show this exact inference frame, so no extrapolation is involved.
            annotated = self._analyzer.render(frame, frame_time) if has_events else frame
        if not has_events:
            return

        occurred_at = datetime.now(UTC)
        batches: tuple[tuple[EventType, list[TrackedObject]], ...] = (
            (EventType.OBJECT_ENTERED, observation.entered),
            (EventType.LINE_CROSSED_IN, observation.crossed_in),
            (EventType.LINE_CROSSED_OUT, observation.crossed_out),
        )
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

    # -- render ------------------------------------------------------------------------------

    def _render_loop(self) -> None:
        interval = 1.0 / self._options.stream_fps
        last_seq = 0
        while not self._stop.is_set():
            started = time.monotonic()
            with self._frame_ready:
                while self._frame_seq == last_seq and not self._stop.is_set():
                    self._frame_ready.wait(timeout=1.0)
                frame = self._latest_frame
                frame_time = self._latest_frame_time
                last_seq = self._frame_seq
            if self._stop.is_set():
                break
            if frame is None:
                continue
            try:
                with self._state_lock:
                    annotated = self._analyzer.render(frame, frame_time)
                jpeg = encode_jpeg(annotated, self._options.jpeg_quality)
            except Exception:
                logger.exception("Rendering failed", extra={"camera_id": self.config.id})
            else:
                with self._output_lock:
                    self._latest_jpeg = jpeg
                    self._jpeg_seq += 1
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def _save_snapshot(self, frame: Frame, occurred_at: datetime) -> str | None:
        relative = Path(
            occurred_at.strftime("%Y/%m/%d"),
            f"cam{self.config.id}-{occurred_at.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}.jpg",
        )
        target = self._options.snapshots_dir / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                encode_jpeg(frame, quality=75, max_width=self._options.snapshot_max_width)
            )
        except OSError, ValueError:
            logger.exception("Could not save snapshot", extra={"camera_id": self.config.id})
            return None
        return relative.as_posix()
