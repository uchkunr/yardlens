"""Check counting against a recording whose count is known.

    uv run yardlens-eval bay-1.mp4 --expected 120 --line 0.36,0.30,0.46,0.62 \\
        --zone "0.2,0.75 1,0.75 1,1 0.2,1" --model bay-sacks.pt --classes truck,sack --cargo sack

Runs the recording through the same steps as a loading-bay camera (analysis region, detection,
tracking, line counting, loading sessions) as fast as the machine allows, at the camera's
analysis rate, then prints the sessions and compares the cargo counted with ``--expected``. Exits
with status 1 when they differ by more than ``--tolerance``, so it can guard a model or a change
in a script. Without ``--zone`` it reports line crossings only.
"""

import argparse
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from yardlens_api.core.config import get_settings
from yardlens_api.core.enums import LOADING_VEHICLE_CLASSES
from yardlens_api.schemas.camera import CountingLine, LoadingZone, ZonePoint
from yardlens_api.schemas.settings import DetectionSettings
from yardlens_api.services.analyzer import FrameAnalyzer
from yardlens_api.services.camera_worker import downscale
from yardlens_api.services.detector import Detector, YoloDetector
from yardlens_api.services.loading import LoadingTracker, SessionChange, SessionState
from yardlens_api.services.pacing import analysis_region, crop_region, input_size
from yardlens_api.services.resources import plan_resources

type Frame = NDArray[np.uint8]

# Sessions are dated from this moment plus the position in the recording.
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(slots=True)
class Evaluation:
    frames: int = 0
    line_in: int = 0
    line_out: int = 0
    sessions: list[SessionState] = field(default_factory=list[SessionState])

    @property
    def loaded(self) -> int:
        return sum(session.loaded for session in self.sessions)


def evaluate_frames(
    frames: Iterator[tuple[float, Frame]],
    *,
    detector: Detector,
    settings: DetectionSettings,
    line: CountingLine | None,
    zone: LoadingZone | None,
    fps: float,
    max_frame_width: int = 1280,
    arrive_after: float = 15.0,
    depart_after: float = 60.0,
) -> Evaluation:
    """Count ``frames`` (seconds into the recording, image) the way a camera worker would."""
    analyzer = FrameAnalyzer(fps=fps, line=line, zone=zone)
    tracker = (
        LoadingTracker(0, zone, arrive_after=arrive_after, depart_after=depart_after)
        if zone is not None
        else None
    )
    region = analysis_region(line, zone)
    cargo = frozenset(settings.cargo_classes)
    result = Evaluation()
    last_t = 0.0
    for t, raw in frames:
        last_t = t
        frame = downscale(raw, max_frame_width)
        crop, (left, top) = crop_region(frame, region)
        detections = detector.detect(
            crop,
            confidence=settings.confidence,
            classes=settings.classes,
            imgsz=input_size(crop.shape[1], crop.shape[0]) if region is not None else None,
        )
        if (left or top) and len(detections) > 0:
            detections.xyxy = detections.xyxy + np.array(
                [left, top, left, top], dtype=detections.xyxy.dtype
            )
        observation = analyzer.process(frame, detections, t)
        result.frames += 1
        if tracker is None:
            continue
        height, width = frame.shape[:2]
        vehicles = [obj for obj in observation.objects if obj.class_name in LOADING_VEHICLE_CLASSES]
        update = tracker.update(
            t=t,
            now=_EPOCH + timedelta(seconds=t),
            parked=tracker.parked(vehicles, width, height),
            loaded=sum(1 for obj in observation.crossed_in if obj.class_name in cargo),
            unloaded=sum(1 for obj in observation.crossed_out if obj.class_name in cargo),
        )
        if update is not None and update.change is SessionChange.CLOSED:
            result.sessions.append(update.session)
    if tracker is not None:
        closed = tracker.close(_EPOCH + timedelta(seconds=last_t), last_t)
        if closed is not None:
            result.sessions.append(closed.session)
    result.line_in, result.line_out = analyzer.line_in, analyzer.line_out
    return result


def read_frames(video: Path, fps: float) -> Iterator[tuple[float, Frame]]:
    """Every frame of ``video`` that a camera analysing at ``fps`` would take."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise SystemExit(f"Cannot open {video}")
    native = capture.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1.0, native / fps)
    index, next_index = 0, 0.0
    try:
        while capture.grab():
            if index >= next_index:
                ok, frame = capture.retrieve()
                if ok:
                    yield index / native, cast("Frame", frame)
                next_index += step
            index += 1
    finally:
        capture.release()


def _points(text: str) -> list[float]:
    return [float(value) for value in text.replace(",", " ").split()]


def _clock(moment: datetime | None) -> str:
    if moment is None:
        return "-"
    seconds = int((moment - _EPOCH).total_seconds())
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def main() -> None:
    config = get_settings()
    parser = argparse.ArgumentParser(description="Check counting against a known count.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--expected", type=int, help="cargo actually loaded in the recording")
    parser.add_argument("--tolerance", type=int, default=0, help="allowed difference")
    parser.add_argument("--line", required=True, help="x1,y1,x2,y2 in 0-1 frame coordinates")
    parser.add_argument("--zone", help='loading zone, "x,y x,y x,y ..." in 0-1 coordinates')
    parser.add_argument("--model", default=config.default_model)
    parser.add_argument("--classes", default="truck,sack")
    parser.add_argument("--cargo", default="sack")
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--fps", type=float, default=10.0, help="the camera's analysis rate")
    args = parser.parse_args()

    line_values = _points(args.line)
    if len(line_values) != 4:
        parser.error("--line needs four numbers: x1,y1,x2,y2")
    line = CountingLine(x1=line_values[0], y1=line_values[1], x2=line_values[2], y2=line_values[3])
    zone = None
    if args.zone:
        values = _points(args.zone)
        if len(values) < 6 or len(values) % 2:
            parser.error("--zone needs at least three x,y points")
        zone = LoadingZone(
            points=[ZonePoint(x=x, y=y) for x, y in zip(values[::2], values[1::2], strict=True)]
        )
    settings = DetectionSettings(
        model=args.model,
        confidence=args.confidence,
        classes=[name.strip() for name in args.classes.split(",") if name.strip()],
        cargo_classes=[name.strip() for name in args.cargo.split(",") if name.strip()],
    )

    plan = plan_resources(inference_threads=config.inference_threads)
    detector = YoloDetector(
        models_dir=config.models_dir,
        model_name=args.model,
        device=config.inference_device,
        backend=config.inference_backend,
        cpu_threads=plan.inference_threads,
    )
    detector.load(args.model)
    while detector.status.optimizing:
        time.sleep(0.5)
    missing = sorted(set(settings.classes) - set(detector.class_names))
    if missing:
        parser.error(f"{args.model} does not detect: {', '.join(missing)}")

    started = time.monotonic()
    result = evaluate_frames(
        read_frames(args.video, args.fps),
        detector=detector,
        settings=settings,
        line=line,
        zone=zone,
        fps=args.fps,
        max_frame_width=config.max_frame_width,
        arrive_after=config.loading_arrive_seconds,
        depart_after=config.loading_depart_seconds,
    )
    elapsed = time.monotonic() - started
    print(f"{args.video.name}: {result.frames} frames at {args.fps:g} fps in {elapsed:.0f} s")
    print(f"Line crossings: in {result.line_in}, out {result.line_out}")
    if zone is not None:
        print("Sessions (time into the recording):")
        for number, session in enumerate(result.sessions, start=1):
            print(
                f"  {number:>2}. {_clock(session.arrived_at)} - {_clock(session.departed_at)}"
                f"  loaded {session.loaded}, unloaded {session.unloaded}"
            )
    counted = result.loaded if zone is not None else result.line_in
    if args.expected is None:
        print(f"Counted: {counted}")
        return
    difference = counted - args.expected
    share = abs(difference) / args.expected * 100 if args.expected else 0.0
    verdict = "PASS" if abs(difference) <= args.tolerance else "FAIL"
    print(f"Counted {counted}, expected {args.expected}: {difference:+d} ({share:.1f}%) {verdict}")
    if verdict == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
