import time
from collections.abc import Callable
from pathlib import Path

from tests.conftest import FakeDetector, make_detections, write_test_video
from yardlens_api.core.enums import Activity, CameraStatus, SourceType
from yardlens_api.schemas.camera import CountingLine, LoadingZone, ZonePoint
from yardlens_api.schemas.settings import CameraDetection, DetectionSettings
from yardlens_api.services.camera_worker import (
    CameraConfig,
    CameraWorker,
    WorkerEvent,
    WorkerOptions,
)
from yardlens_api.services.governor import Limits
from yardlens_api.services.loading import SessionState


class _NullListener:
    def on_event(self, event: WorkerEvent) -> None:
        pass

    def on_status(self, camera_id: int, status: CameraStatus, last_error: str | None) -> None:
        pass

    def on_session(self, session: SessionState) -> None:
        pass


def _wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


def test_rendering_runs_only_while_someone_asks_for_frames(tmp_path: Path) -> None:
    clip = write_test_video(tmp_path / "clip.mp4")
    worker = CameraWorker(
        CameraConfig(
            id=1,
            name="Gate",
            source_type=SourceType.FILE,
            url=str(clip),
            inference_fps=5.0,
            line=None,
        ),
        detector=FakeDetector(),
        detection_settings=DetectionSettings,
        listener=_NullListener(),
        options=WorkerOptions(
            snapshots_dir=tmp_path / "snapshots", base_dir=tmp_path, viewer_idle_seconds=0.3
        ),
    )
    worker.start()
    try:
        _wait_until(lambda: worker.state().status is CameraStatus.ONLINE)
        time.sleep(0.5)
        assert worker.latest_jpeg() == (0, None), "rendered although nobody was watching"

        # Asking for a frame wakes the render thread.
        _wait_until(lambda: worker.latest_jpeg()[1] is not None)

        # Once viewers are gone it stops again and drops the stale frame.
        time.sleep(0.8)
        assert worker.latest_jpeg()[1] is None
    finally:
        worker.stop()
        worker.join(5.0)


def test_loading_bays_analyse_only_the_zone_and_line(tmp_path: Path) -> None:
    clip = write_test_video(tmp_path / "clip.mp4", size=(640, 360))
    detector = FakeDetector()
    worker = CameraWorker(
        CameraConfig(
            id=1,
            name="Bay",
            source_type=SourceType.FILE,
            url=str(clip),
            inference_fps=10.0,
            line=CountingLine(x1=0.4, y1=0.3, x2=0.6, y2=0.3),
            zone=LoadingZone(
                points=[ZonePoint(x=0.4, y=0.4), ZonePoint(x=0.6, y=0.4), ZonePoint(x=0.5, y=0.6)]
            ),
        ),
        detector=detector,
        detection_settings=DetectionSettings,
        listener=_NullListener(),
        options=WorkerOptions(snapshots_dir=tmp_path / "snapshots", base_dir=tmp_path),
    )
    worker.start()
    try:
        _wait_until(lambda: detector.calls >= 3)
    finally:
        worker.stop()
        worker.join(5.0)
    # The zone and line plus margins are a 384 x 252 px region, sent to the model as 384 px.
    assert set(detector.sizes) == {384}


def test_idle_cameras_slow_down(tmp_path: Path) -> None:
    clip = write_test_video(tmp_path / "clip.mp4")
    detector = FakeDetector(lambda _: make_detections([], []))
    worker = CameraWorker(
        CameraConfig(
            id=1,
            name="Gate",
            source_type=SourceType.FILE,
            url=str(clip),
            inference_fps=10.0,
            line=None,
        ),
        detector=detector,
        detection_settings=DetectionSettings,
        listener=_NullListener(),
        options=WorkerOptions(
            snapshots_dir=tmp_path / "snapshots", base_dir=tmp_path, active_hold_seconds=0.3
        ),
        limits=lambda: Limits(adaptive=True, idle_fps=1.0),
    )
    worker.start()
    try:
        _wait_until(lambda: worker.state().activity is Activity.IDLE)
        calls = detector.calls
        time.sleep(1.2)
        # At 10 fps this would be ~12 calls; idle runs one a second.
        assert detector.calls - calls <= 2
        assert worker.state().target_fps == 1.0
    finally:
        worker.stop()
        worker.join(5.0)


def test_a_camera_detects_its_own_classes(tmp_path: Path) -> None:
    clip = write_test_video(tmp_path / "clip.mp4")
    detector = FakeDetector()
    worker = CameraWorker(
        CameraConfig(
            id=1,
            name="Bay",
            source_type=SourceType.FILE,
            url=str(clip),
            inference_fps=10.0,
            line=None,
            detection=CameraDetection(classes=["truck"]),
        ),
        detector=detector,
        detection_settings=DetectionSettings,
        listener=_NullListener(),
        options=WorkerOptions(snapshots_dir=tmp_path / "snapshots", base_dir=tmp_path),
    )
    worker.start()
    try:
        _wait_until(lambda: detector.calls >= 2)
    finally:
        worker.stop()
        worker.join(5.0)
    assert detector.classes[0] == ["truck"]
