"""Measure what one camera costs on this machine, before trusting it with real ones.

    uv run yardlens-bench data/videos/yard.mp4
    uv run yardlens-bench clip.mp4 --backend pytorch --seconds 30

Runs the same pipeline as a camera worker on a recorded video: decoding, detection, tracking and
the live view, with the same thread plan, model and backend the API would use. Prints how long
each stage takes, the CPU it uses, and an estimate of the load per camera in each pacing state
and of how many cameras fit under the CPU limit.
"""

import argparse
import platform
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import psutil

from yardlens_api.core.config import get_settings
from yardlens_api.core.enums import DEFAULT_CLASSES
from yardlens_api.services.analyzer import FrameAnalyzer
from yardlens_api.services.camera_worker import Frame, downscale, encode_jpeg
from yardlens_api.services.detector import YoloDetector
from yardlens_api.services.governor import STANDBY_FPS
from yardlens_api.services.resources import apply_process_plan, plan_resources
from yardlens_api.services.sources import ReaderKind, ResolvedSource, VideoCaptureReader

_DECODE_FRAMES = 250
_OPTIMIZE_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class Stage:
    wall_ms: list[float]
    cpu_ms: float
    """Process CPU time per item, over all threads."""

    @property
    def mean_ms(self) -> float:
        return statistics.fmean(self.wall_ms) if self.wall_ms else 0.0

    @property
    def p95_ms(self) -> float:
        return float(np.percentile(self.wall_ms, 95)) if self.wall_ms else 0.0


def _measure(count: int, step: Callable[[int], bool]) -> Stage:
    """Run ``step`` up to ``count`` times (until it returns False), timing each call."""
    times: list[float] = []
    cpu_started = time.process_time()
    for index in range(count):
        started = time.perf_counter()
        if not step(index):
            break
        times.append((time.perf_counter() - started) * 1000)
    cpu = (time.process_time() - cpu_started) * 1000
    return Stage(wall_ms=times, cpu_ms=cpu / max(1, len(times)))


def _cpu_name() -> str:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    elif sys.platform == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def main() -> None:
    config = get_settings()
    parser = argparse.ArgumentParser(description="Measure what one camera costs on this machine.")
    parser.add_argument("video", type=Path, nargs="?", default=config.videos_dir / "vehicles.mp4")
    parser.add_argument("--model", default=config.default_model)
    parser.add_argument("--device", default=config.inference_device)
    parser.add_argument(
        "--backend", default=config.inference_backend, choices=["auto", "pytorch", "openvino"]
    )
    parser.add_argument("--fps", type=float, default=10.0, help="camera rate while active")
    parser.add_argument("--frames", type=int, default=150, help="frames to run inference on")
    parser.add_argument("--limit", type=float, default=75.0, help="CPU limit, percent")
    args = parser.parse_args()
    video: Path = args.video
    if not video.is_file():
        parser.error(f"video not found: {video}")

    plan = plan_resources(inference_threads=config.inference_threads)
    apply_process_plan(plan, low_priority=False)
    memory = psutil.virtual_memory()
    print(f"Machine   {_cpu_name()}")
    print(
        f"          {plan.physical_cores} cores / {plan.logical_cpus} threads, "
        f"{memory.total / 2**30:.1f} GB RAM"
    )

    # -- decoding ----------------------------------------------------------------------------
    source = ResolvedSource(uri=str(video), kind=ReaderKind.VIDEO, loop=True)
    reader = VideoCaptureReader(
        source, hardware_decode=config.hardware_decode, threads=plan.decode_threads
    )
    try:
        first = reader.read()
        if first is None:
            parser.error(f"could not decode {video}")
        native_fps = reader.native_fps or 25.0
        print(
            f"Video     {video.name}: {first.shape[1]}x{first.shape[0]} at {native_fps:g} fps, "
            f"decoded on the {'GPU' if reader.hardware_decoding else 'CPU'}"
        )
        grab = _measure(_DECODE_FRAMES, lambda _: reader.grab())
        convert = _measure(_DECODE_FRAMES, lambda _: reader.read() is not None)
        frames: list[Frame] = []

        def keep(_: int) -> bool:
            frame = reader.read()
            if frame is None:
                return False
            frames.append(downscale(frame, config.max_frame_width))
            return True

        _measure(min(args.frames, 60), keep)
    finally:
        reader.close()
    convert_ms = max(0.0, convert.cpu_ms - grab.cpu_ms)
    print(
        f"Decode    {grab.cpu_ms:.1f} ms CPU per frame to keep up with the stream, "
        f"+{convert_ms:.1f} ms for each frame that is used"
    )

    # -- inference ---------------------------------------------------------------------------
    detector = YoloDetector(
        models_dir=config.models_dir,
        model_name=args.model,
        device=args.device,
        backend=args.backend,
        cpu_threads=plan.inference_threads,
    )
    detector.load(args.model)
    if detector.status.optimizing:
        print("Model     converting to OpenVINO (once; cached for the API)...", flush=True)
        deadline = time.monotonic() + _OPTIMIZE_TIMEOUT_SECONDS
        while detector.status.optimizing and time.monotonic() < deadline:
            time.sleep(0.5)
    status = detector.status
    print(
        f"Model     {status.model} on {status.backend} ({status.device}, "
        f"{status.threads} CPU threads)"
    )
    analyzer = FrameAnalyzer(fps=args.fps)
    classes = list(DEFAULT_CLASSES)
    detections_seen = 0

    def infer(index: int) -> bool:
        nonlocal detections_seen
        frame = frames[index % len(frames)]
        detections = detector.detect(frame, confidence=0.35, classes=classes)
        detections_seen += len(detections)
        analyzer.process(frame, detections, index / args.fps)
        return True

    infer(0)  # warm-up
    inference = _measure(args.frames, infer)
    render = _measure(
        min(args.frames, 60),
        lambda index: bool(
            encode_jpeg(analyzer.render(frames[index % len(frames)], index / args.fps), 80)
        ),
    )
    print(
        f"Inference {inference.mean_ms:.1f} ms per frame (p95 {inference.p95_ms:.1f}), "
        f"{inference.cpu_ms:.1f} ms CPU; {detections_seen / max(1, args.frames):.1f} objects/frame"
    )
    print(f"Live view {render.cpu_ms:.1f} ms CPU per frame, only while someone watches")

    # -- estimate ----------------------------------------------------------------------------
    capacity_ms = plan.logical_cpus * 1000.0  # CPU-ms per second, as the OS counts load

    def load(fps: float) -> float:
        per_second = grab.cpu_ms * native_fps + (convert_ms + inference.cpu_ms) * fps
        return per_second / capacity_ms * 100

    print()
    print("Load per camera (share of the whole CPU):")
    for label, fps in (
        ("idle      1 fps", 1.0),
        (f"standby   {STANDBY_FPS:g} fps", STANDBY_FPS),
        (f"active   {args.fps:g} fps", args.fps),
    ):
        print(f"  {label:<18} {load(fps):5.1f}%")
    active = load(args.fps)
    fits = int(args.limit // active) if active > 0 else 0
    print(
        f"About {fits} camera(s) can be active at the same time under the {args.limit:g}% limit; "
        f"idle ones cost {load(1.0):.1f}% each."
    )
    if inference.mean_ms * args.fps > 1000 * 0.8:
        print(
            "Inference alone takes most of each frame interval: lower the camera rate or the "
            "model size, or add inference threads (YARDLENS_INFERENCE_THREADS)."
        )


if __name__ == "__main__":
    main()
