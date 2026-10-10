"""Per-camera frame analysis: tracking, line counting and annotation.

``FrameAnalyzer`` is deliberately free of threads and I/O so it can be unit-tested with synthetic
detections. The camera worker feeds it one ``sv.Detections`` per inference tick (``process``) and
asks it to draw the latest tracks onto every displayed frame (``render``). Display frames arrive
far more often than inference results, so ``render`` extrapolates each box from its recent motion
instead of leaving it where the object was at the last inference.
"""

from collections import Counter
from dataclasses import dataclass, field, replace

import cv2
import numpy as np
import supervision as sv
from numpy.typing import NDArray
from trackers import ByteTrackTracker

from yardlens_api.schemas.camera import CountingLine

type Frame = NDArray[np.uint8]
type Box = tuple[float, float, float, float]

_TRACK_FORGET_AFTER_FRAMES = 300
# Weight of the newest measurement when smoothing box velocity between inference ticks.
_VELOCITY_SMOOTHING = 0.7
# Motion measured across a longer gap (e.g. a track lost and found again) is not trusted.
_MAX_VELOCITY_GAP_SECONDS = 2.0
_STILL: Box = (0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class TrackedObject:
    tracker_id: int
    class_id: int
    class_name: str
    confidence: float
    box: Box


@dataclass(slots=True)
class Observation:
    entered: list[TrackedObject] = field(default_factory=list[TrackedObject])
    crossed_in: list[TrackedObject] = field(default_factory=list[TrackedObject])
    crossed_out: list[TrackedObject] = field(default_factory=list[TrackedObject])
    visible: dict[str, int] = field(default_factory=dict[str, int])


@dataclass(frozen=True, slots=True)
class _Motion:
    box: Box
    seen_at: float
    velocity: Box  # pixels per second for each box coordinate


def line_to_pixels(line: CountingLine, width: int, height: int) -> tuple[sv.Point, sv.Point]:
    return (
        sv.Point(x=line.x1 * width, y=line.y1 * height),
        sv.Point(x=line.x2 * width, y=line.y2 * height),
    )


class LineCounter:
    """Counts tracked objects crossing a line given in normalized coordinates.

    "In" follows supervision's convention: crossing from the right-hand side of the
    start→end vector to its left-hand side.
    """

    def __init__(self, line: CountingLine) -> None:
        self.line = line
        self._zone: sv.LineZone | None = None
        self._size: tuple[int, int] | None = None
        self.in_count = 0
        self.out_count = 0

    def zone_for(self, width: int, height: int) -> sv.LineZone:
        if self._zone is None or self._size != (width, height):
            start, end = line_to_pixels(self.line, width, height)
            self._zone = sv.LineZone(
                start=start, end=end, triggering_anchors=(sv.Position.BOTTOM_CENTER,)
            )
            self._size = (width, height)
        return self._zone

    def trigger(
        self, detections: sv.Detections, width: int, height: int
    ) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
        zone = self.zone_for(width, height)
        crossed_in, crossed_out = zone.trigger(detections)
        self.in_count += int(np.count_nonzero(crossed_in))
        self.out_count += int(np.count_nonzero(crossed_out))
        return crossed_in, crossed_out


class FrameAnalyzer:
    def __init__(self, *, fps: float, line: CountingLine | None = None, min_hits: int = 1) -> None:
        # ByteTrack confirms a track after two consecutive matches; unconfirmed detections come
        # back with tracker_id -1 and are ignored. The lost-track buffer is ~3 s at any rate.
        self._tracker = ByteTrackTracker(
            lost_track_buffer=max(int(fps * 3), 5),
            frame_rate=fps,
            track_activation_threshold=0.4,
            high_conf_det_threshold=0.5,
            minimum_consecutive_frames=2,
            minimum_iou_threshold=0.1,
        )
        self._min_hits = min_hits
        self._hits: Counter[int] = Counter()
        self._last_seen: dict[int, int] = {}
        self._announced: set[int] = set()
        self._frame_index = 0
        self._counter: LineCounter | None = LineCounter(line) if line else None
        self._line_in_offset = 0
        self._line_out_offset = 0
        self._annotators: _Annotators | None = None
        self._motion: dict[int, _Motion] = {}
        self._current: list[TrackedObject] = []
        # Boxes are extrapolated at most this far past the inference frame they came from.
        self._max_extrapolation = max(1.5 / fps, 0.3)

    @property
    def line(self) -> CountingLine | None:
        return self._counter.line if self._counter else None

    @property
    def line_in(self) -> int:
        return self._line_in_offset + (self._counter.in_count if self._counter else 0)

    @property
    def line_out(self) -> int:
        return self._line_out_offset + (self._counter.out_count if self._counter else 0)

    def set_line(self, line: CountingLine | None) -> None:
        if line == self.line:
            return
        if self._counter:
            self._line_in_offset += self._counter.in_count
            self._line_out_offset += self._counter.out_count
        self._counter = LineCounter(line) if line else None

    def process(self, frame: Frame, detections: sv.Detections, timestamp: float) -> Observation:
        """Track and count one inference frame captured at ``timestamp`` (monotonic seconds)."""
        height, width = frame.shape[:2]
        self._frame_index += 1
        tracked = _confirmed(self._tracker.update(detections))
        objects = _to_objects(tracked)

        observation = Observation(visible=dict(Counter(obj.class_name for obj in objects)))
        for obj in objects:
            self._hits[obj.tracker_id] += 1
            self._last_seen[obj.tracker_id] = self._frame_index
            if (
                obj.tracker_id not in self._announced
                and self._hits[obj.tracker_id] >= self._min_hits
            ):
                self._announced.add(obj.tracker_id)
                observation.entered.append(obj)
        self._update_motion(objects, timestamp)
        self._current = objects
        self._forget_stale_tracks()

        if self._counter and len(tracked) > 0:
            crossed_in, crossed_out = self._counter.trigger(tracked, width, height)
            observation.crossed_in = [
                obj for obj, hit in zip(objects, crossed_in, strict=True) if hit
            ]
            observation.crossed_out = [
                obj for obj, hit in zip(objects, crossed_out, strict=True) if hit
            ]
        return observation

    def predicted(self, timestamp: float) -> list[TrackedObject]:
        """Latest tracks with boxes moved to where they are expected at ``timestamp``."""
        objects: list[TrackedObject] = []
        for obj in self._current:
            motion = self._motion.get(obj.tracker_id)
            if motion is None:
                objects.append(obj)
                continue
            elapsed = min(max(timestamp - motion.seen_at, 0.0), self._max_extrapolation)
            box = (
                motion.box[0] + motion.velocity[0] * elapsed,
                motion.box[1] + motion.velocity[1] * elapsed,
                motion.box[2] + motion.velocity[2] * elapsed,
                motion.box[3] + motion.velocity[3] * elapsed,
            )
            objects.append(replace(obj, box=box))
        return objects

    def render(self, frame: Frame, timestamp: float) -> Frame:
        """Draw the latest tracks (extrapolated to ``timestamp``) and the counting line."""
        height, width = frame.shape[:2]
        if self._annotators is None or self._annotators.size != (width, height):
            self._annotators = _Annotators.for_size(width, height)
        annotators = self._annotators
        scene = frame.copy()
        objects = self.predicted(timestamp)
        if objects:
            boxes = np.array([obj.box for obj in objects], dtype=np.float32)
            boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width - 1)
            boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height - 1)
            detections = sv.Detections(
                xyxy=boxes,
                class_id=np.array([obj.class_id for obj in objects], dtype=int),
                tracker_id=np.array([obj.tracker_id for obj in objects], dtype=int),
            )
            labels = [f"{obj.class_name} #{obj.tracker_id}" for obj in objects]
            scene = annotators.box.annotate(scene, detections)
            scene = annotators.label.annotate(scene, detections, labels=labels)
        if self._counter:
            scene = annotators.line.annotate(scene, self._counter.zone_for(width, height))
        return scene

    def _update_motion(self, objects: list[TrackedObject], timestamp: float) -> None:
        for obj in objects:
            previous = self._motion.get(obj.tracker_id)
            velocity = _STILL
            if previous is not None:
                elapsed = timestamp - previous.seen_at
                if 0 < elapsed <= _MAX_VELOCITY_GAP_SECONDS:
                    measured = [
                        (now - before) / elapsed
                        for now, before in zip(obj.box, previous.box, strict=True)
                    ]
                    blended = [
                        _VELOCITY_SMOOTHING * new + (1 - _VELOCITY_SMOOTHING) * old
                        for new, old in zip(measured, previous.velocity, strict=True)
                    ]
                    velocity = (blended[0], blended[1], blended[2], blended[3])
            self._motion[obj.tracker_id] = _Motion(
                box=obj.box, seen_at=timestamp, velocity=velocity
            )

    def _forget_stale_tracks(self) -> None:
        cutoff = self._frame_index - _TRACK_FORGET_AFTER_FRAMES
        stale = [tracker_id for tracker_id, seen in self._last_seen.items() if seen < cutoff]
        for tracker_id in stale:
            del self._last_seen[tracker_id]
            self._hits.pop(tracker_id, None)
            self._announced.discard(tracker_id)
            self._motion.pop(tracker_id, None)


@dataclass(frozen=True, slots=True)
class _Annotators:
    size: tuple[int, int]
    box: sv.BoxAnnotator
    label: sv.LabelAnnotator
    line: sv.LineZoneAnnotator

    @classmethod
    def for_size(cls, width: int, height: int) -> _Annotators:
        thickness = sv.calculate_optimal_line_thickness((width, height))
        text_scale = sv.calculate_optimal_text_scale((width, height))
        return cls(
            size=(width, height),
            box=sv.BoxAnnotator(thickness=thickness, color_lookup=sv.ColorLookup.CLASS),
            label=sv.LabelAnnotator(
                text_scale=text_scale * 0.8,
                text_padding=max(2, thickness * 2),
                color_lookup=sv.ColorLookup.CLASS,
            ),
            line=sv.LineZoneAnnotator(
                thickness=thickness,
                text_scale=text_scale * 0.8,
                text_thickness=max(1, thickness // 2),
            ),
        )


def _confirmed(detections: sv.Detections) -> sv.Detections:
    if len(detections) == 0 or detections.tracker_id is None:
        return sv.Detections.empty()
    confirmed = detections[detections.tracker_id >= 0]
    assert isinstance(confirmed, sv.Detections)
    return confirmed


def _to_objects(detections: sv.Detections) -> list[TrackedObject]:
    if len(detections) == 0 or detections.tracker_id is None:
        return []
    raw_names = detections.data.get("class_name")
    names: list[str] = [str(name) for name in raw_names] if raw_names is not None else []
    confidences = (
        detections.confidence if detections.confidence is not None else np.ones(len(detections))
    )
    class_ids = (
        detections.class_id if detections.class_id is not None else np.zeros(len(detections))
    )
    objects: list[TrackedObject] = []
    for index in range(len(detections)):
        x1, y1, x2, y2 = (float(value) for value in detections.xyxy[index])
        objects.append(
            TrackedObject(
                tracker_id=int(detections.tracker_id[index]),
                class_id=int(class_ids[index]),
                class_name=names[index] if index < len(names) else "object",
                confidence=float(confidences[index]),
                box=(x1, y1, x2, y2),
            )
        )
    return objects


def highlight(frame: Frame, box: Box) -> Frame:
    """Draw an emphasised outline around one object, used for event snapshots."""
    height, width = frame.shape[:2]
    thickness = max(2, sv.calculate_optimal_line_thickness((width, height)) + 1)
    x1, y1, x2, y2 = (round(value) for value in box)
    pad = thickness * 2
    scene = frame.copy()
    cv2.rectangle(
        scene,
        (max(0, x1 - pad), max(0, y1 - pad)),
        (min(width - 1, x2 + pad), min(height - 1, y2 + pad)),
        (255, 255, 255),
        thickness,
    )
    return scene
