"""Load balancing: the governor, per-camera pacing, the thread plan and the inference backend."""

import json
import time
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import FakeDetector, make_detections
from yardlens_api.core.enums import Activity
from yardlens_api.schemas.camera import CountingLine, LoadingZone, ZonePoint
from yardlens_api.schemas.settings import CameraDetection, DetectionSettings, effective_detection
from yardlens_api.services.analyzer import FrameAnalyzer
from yardlens_api.services.detector import (
    Backend,
    InferencePlan,
    ModelPool,
    openvino_dir,
    openvino_ready,
    plan_inference,
)
from yardlens_api.services.governor import (
    MIN_COUNTING_FPS,
    Budget,
    Governor,
    Limits,
    LoadLevel,
    ResourceSample,
    limits_for,
)
from yardlens_api.services.pacing import (
    MIN_INPUT_SIZE,
    Pacer,
    analysis_region,
    crop_region,
    input_size,
)
from yardlens_api.services.plate_stream import PlateStream
from yardlens_api.services.resources import plan_resources
from yardlens_api.services.sources import Frame


def _sample(cpu: float, memory: float = 40.0, busy: float = 0.0) -> ResourceSample:
    return ResourceSample(cpu_percent=cpu, memory_percent=memory, detector_busy=busy)


# -- governor ------------------------------------------------------------------------------------


def test_governor_steps_up_after_sustained_load_and_relaxes_slowly() -> None:
    governor = Governor(raise_after=2, lower_after=3)
    assert governor.update(_sample(90), 75) is LoadLevel.NORMAL  # one spike is not enough
    assert governor.update(_sample(90), 75) is LoadLevel.BUSY
    for _ in range(4):
        governor.update(_sample(95), 75)
    assert governor.level is LoadLevel.OVERLOAD
    governor.update(_sample(99), 75)
    governor.update(_sample(99), 75)
    assert governor.level is LoadLevel.OVERLOAD  # the top level is the top

    # Within the relax margin (limit - 15) nothing changes either way.
    for _ in range(10):
        governor.update(_sample(70), 75)
    assert governor.level is LoadLevel.OVERLOAD
    for _ in range(3):
        governor.update(_sample(30), 75)
    assert governor.level is LoadLevel.HIGH


def test_governor_counts_the_detector_and_memory_as_load() -> None:
    governor = Governor(raise_after=1)
    assert governor.update(_sample(20, busy=0.95), 75) is LoadLevel.BUSY
    governor = Governor()
    assert governor.update(_sample(10, memory=95), 75) is LoadLevel.HIGH


def test_limits_give_up_the_live_view_before_counting() -> None:
    budget = Budget(stream_fps=15, jpeg_quality=80, idle_fps=1.0)
    normal = limits_for(LoadLevel.NORMAL, budget)
    busy = limits_for(LoadLevel.BUSY, budget)
    overload = limits_for(LoadLevel.OVERLOAD, budget)
    assert (normal.stream_fps, normal.fps_scale) == (15, 1.0)
    assert busy.stream_fps < normal.stream_fps
    assert busy.fps_scale == 1.0
    assert overload.fps_scale < 1.0
    # Counting never drops below the floor, unless the camera is configured lower.
    assert overload.active_fps(10) >= MIN_COUNTING_FPS
    assert overload.active_fps(2) == 2


# -- pacing --------------------------------------------------------------------------------------


def test_pacer_runs_full_rate_while_things_move_then_rests() -> None:
    limits = Limits(adaptive=True, idle_fps=1.0)
    pacer = Pacer(10, hold_seconds=5)
    pacer.observe(0.0, moving=False)  # the first frame always counts as movement
    assert pacer.activity(1.0) is Activity.ACTIVE
    assert pacer.fps(1.0, limits) == 10
    pacer.observe(6.0, moving=False)
    assert pacer.activity(6.0) is Activity.IDLE
    assert pacer.fps(6.0, limits) == 1.0
    pacer.observe(7.0, moving=False, standby=True)
    assert pacer.activity(7.0) is Activity.STANDBY
    assert 1.0 < pacer.fps(7.0, limits) < 10
    pacer.observe(8.0, moving=True, standby=True)
    assert pacer.fps(8.0, limits) == 10


def test_pacer_without_adaptive_mode_keeps_the_configured_rate() -> None:
    pacer = Pacer(8, hold_seconds=1)
    pacer.observe(0.0, moving=False)
    pacer.observe(10.0, moving=False)
    assert pacer.fps(10.0, Limits(adaptive=False)) == 8


def _zone(*points: tuple[float, float]) -> LoadingZone:
    return LoadingZone(points=[ZonePoint(x=x, y=y) for x, y in points])


def test_analysis_region_covers_zone_and_line_with_a_margin() -> None:
    zone = _zone((0.4, 0.6), (0.6, 0.6), (0.6, 0.8), (0.4, 0.8))
    line = CountingLine(x1=0.45, y1=0.5, x2=0.55, y2=0.5)
    region = analysis_region(line, zone, margin=0.1)
    assert region is not None
    assert (region.left, region.top, region.right, region.bottom) == pytest.approx(
        (0.3, 0.4, 0.7, 0.9)
    )
    assert analysis_region(line, None) is None, "only loading bays are cropped"
    big = _zone((0.0, 0.2), (1.0, 0.2), (1.0, 1.0), (0.0, 1.0))
    assert analysis_region(None, big) is None, "cropping most of the frame saves nothing"


def test_crop_region_and_input_size() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    region = analysis_region(None, _zone((0.5, 0.5), (0.7, 0.5), (0.6, 0.7)), margin=0.0)
    crop, (left, top) = crop_region(frame, region)
    assert (left, top) == (640, 360)
    assert crop.shape[:2] == (144, 256)
    assert input_size(256, 144) == MIN_INPUT_SIZE
    assert input_size(500, 300) == 512
    assert input_size(1280, 720) == 640
    whole, offset = crop_region(frame, None)
    assert whole is frame
    assert offset == (0, 0)


# -- movement ------------------------------------------------------------------------------------


def test_analyzer_tells_moving_objects_from_parked_ones() -> None:
    analyzer = FrameAnalyzer(fps=10)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    observations = [
        analyzer.process(frame, make_detections([(100, 100, 300, 300)], ["car"]), t / 10)
        for t in range(20)
    ]
    assert observations[0].moving, "a new object counts as movement"
    assert not observations[-1].moving, "a parked car is not"

    moving = FrameAnalyzer(fps=10)
    last = None
    for t in range(20):
        x = 100 + t * 30  # 300 px/s across a 1280 px frame
        last = moving.process(frame, make_detections([(x, 100, x + 200, 300)], ["car"]), t / 10)
    assert last is not None
    assert last.moving


# -- resources and backend ----------------------------------------------------------------------


def test_resource_plan_keeps_inference_narrow() -> None:
    six = plan_resources(logical=12, physical=6)
    assert (six.inference_threads, six.decode_threads, six.plate_threads) == (2, 2, 1)
    four = plan_resources(logical=4, physical=4)
    assert (four.inference_threads, four.decode_threads, four.opencv_threads) == (1, 1, 1)
    assert plan_resources(logical=28, physical=20).inference_threads == 4
    assert plan_resources(inference_threads=6, logical=12, physical=6).inference_threads == 6


@pytest.mark.parametrize(
    ("device", "backend", "cuda", "openvino", "expected"),
    [
        ("auto", "auto", True, True, InferencePlan(Backend.PYTORCH, "cuda:0")),
        ("auto", "auto", False, True, InferencePlan(Backend.OPENVINO, "intel:cpu")),
        ("auto", "auto", False, False, InferencePlan(Backend.PYTORCH, "cpu")),
        ("cpu", "pytorch", False, True, InferencePlan(Backend.PYTORCH, "cpu")),
        ("auto", "openvino", True, True, InferencePlan(Backend.OPENVINO, "intel:cpu")),
        ("intel:gpu", "auto", False, True, InferencePlan(Backend.OPENVINO, "intel:gpu")),
        ("intel:gpu", "auto", False, False, InferencePlan(Backend.PYTORCH, "cpu")),
        ("cuda:1", "auto", True, True, InferencePlan(Backend.PYTORCH, "cuda:1")),
    ],
)
def test_plan_inference(
    device: str, backend: str, cuda: bool, openvino: bool, expected: InferencePlan
) -> None:
    assert plan_inference(device, backend, cuda=cuda, mps=False, openvino=openvino) == expected


def test_openvino_copy_is_reused_only_for_the_same_weights(tmp_path: Path) -> None:
    weights = tmp_path / "sacks.pt"
    weights.write_bytes(b"v1")
    assert not openvino_ready(weights)
    exported = openvino_dir(weights)
    exported.mkdir()
    stat = weights.stat()
    (exported / "yardlens.json").write_text(
        json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    )
    assert openvino_ready(weights)
    weights.write_bytes(b"version 2")
    assert not openvino_ready(weights), "retrained weights need a new conversion"


# -- plate stream --------------------------------------------------------------------------------


class _FakeStream:
    def __init__(self) -> None:
        self.closed = False

    @property
    def native_fps(self) -> float | None:
        return 25.0

    @property
    def hardware_decoding(self) -> bool:
        return False

    def grab(self) -> bool:
        time.sleep(0.01)
        return not self.closed

    def retrieve(self) -> Frame | None:
        return np.full((40, 80, 3), 7, dtype=np.uint8)

    def read(self) -> Frame | None:
        return self.retrieve() if self.grab() else None

    def close(self) -> None:
        self.closed = True


def test_plate_stream_opens_on_demand_and_closes_when_no_longer_wanted() -> None:
    opened: list[_FakeStream] = []

    def opener() -> _FakeStream:
        opened.append(_FakeStream())
        return opened[-1]

    stream = PlateStream("rtsp://cam/main", interval=0.02, linger=0.3, opener=opener)
    try:
        assert not opened, "nothing opens until a plate is wanted"
        assert stream.latest() is None
        stream.want()
        deadline = time.monotonic() + 2.0
        while stream.latest() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        frame = stream.latest()
        assert frame is not None
        assert frame.shape == (40, 80, 3)
        deadline = time.monotonic() + 2.0
        while stream.connected and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not stream.connected
        assert opened[0].closed
        assert stream.latest() is None
    finally:
        stream.stop()


# -- per-camera detection -----------------------------------------------------------------------


def test_a_camera_overrides_only_what_it_sets() -> None:
    general = DetectionSettings(
        model="yolo26n.pt",
        confidence=0.35,
        classes=["truck", "person", "suitcase"],
        cargo_classes=["suitcase"],
    )
    assert effective_detection(general, None) is general
    own = effective_detection(general, CameraDetection(model="bay-sacks.pt", classes=["truck"]))
    assert own.model == "bay-sacks.pt"
    assert own.confidence == 0.35
    assert own.classes == ["truck"]
    assert own.cargo_classes == [], "inherited cargo stays within the camera's classes"
    assert effective_detection(general, CameraDetection(confidence=0.6)).confidence == 0.6


def test_model_pool_loads_once_and_unloads_what_is_no_longer_used() -> None:
    loads: list[str] = []

    def load(name: str) -> FakeDetector:
        loads.append(name)
        return FakeDetector()

    pool = ModelPool(load)
    first = pool.get("bay-sacks.pt")
    assert pool.get("bay-sacks.pt") is first
    pool.get("gate.pt")
    assert loads == ["bay-sacks.pt", "gate.pt"]
    pool.retain({"gate.pt"})
    assert pool.models == ["gate.pt"]
