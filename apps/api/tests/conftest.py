from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
import supervision as sv
from fastapi import FastAPI
from numpy.typing import NDArray

from yardlens_api.api.deps import Container
from yardlens_api.core.config import Settings
from yardlens_api.main import create_app
from yardlens_api.services.detector import COCO_CLASSES, DetectorStatus

type Frame = NDArray[np.uint8]
type DetectionScript = Callable[[int], sv.Detections]


def make_detections(
    boxes: Sequence[tuple[float, float, float, float]],
    class_names: Sequence[str],
    confidence: float = 0.9,
) -> sv.Detections:
    if not boxes:
        return sv.Detections.empty()
    class_ids = np.array([COCO_CLASSES.index(name) for name in class_names], dtype=int)
    detections = sv.Detections(
        xyxy=np.array(boxes, dtype=np.float32),
        confidence=np.full(len(boxes), confidence, dtype=np.float32),
        class_id=class_ids,
    )
    detections.data["class_name"] = np.array(list(class_names), dtype=object)
    return detections


def _parked_car(_: int) -> sv.Detections:
    return make_detections([(10, 10, 60, 60)], ["car"])


class FakeDetector:
    """Deterministic detector: returns whatever the script produces for the n-th call."""

    def __init__(self, script: DetectionScript = _parked_car) -> None:
        self._script = script
        self.calls = 0
        self.model = "yolo26n.pt"

    @property
    def status(self) -> DetectorStatus:
        return DetectorStatus(model=self.model, device="cpu", loaded=True)

    @property
    def class_names(self) -> Sequence[str]:
        return COCO_CLASSES

    def load(self, model_name: str) -> None:
        self.model = model_name

    def detect(self, frame: Frame, *, confidence: float, classes: Sequence[str]) -> sv.Detections:
        self.calls += 1
        return self._script(self.calls)


def write_test_video(path: Path, *, frames: int = 12, size: tuple[int, int] = (160, 120)) -> Path:
    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 10.0, (width, height))
    for index in range(frames):
        frame = np.full((height, width, 3), index * 10 % 255, dtype=np.uint8)
        cv2.rectangle(frame, (index * 5, 20), (index * 5 + 30, 50), (0, 255, 0), -1)
        writer.write(frame)
    writer.release()
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        workers_enabled=True,
        cors_origins=["http://localhost:3100"],
    )


@pytest.fixture
def video_file(settings: Settings) -> Path:
    settings.ensure_directories()
    return write_test_video(settings.videos_dir / "clip.mp4")


@pytest.fixture
def detector() -> FakeDetector:
    return FakeDetector()


@pytest.fixture
async def app(settings: Settings, detector: FakeDetector) -> AsyncIterator[FastAPI]:
    application = create_app(settings, detector=detector)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def container(app: FastAPI) -> Container:
    value: Container = app.state.container
    return value


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
