import time
from collections.abc import Callable
from pathlib import Path

from tests.conftest import FakeDetector, write_test_video
from yardlens_api.core.enums import CameraStatus, SourceType
from yardlens_api.schemas.settings import DetectionSettings
from yardlens_api.services.camera_worker import (
    CameraConfig,
    CameraWorker,
    WorkerEvent,
    WorkerOptions,
)
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
