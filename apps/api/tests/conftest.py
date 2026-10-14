import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
import pytest
import supervision as sv
import uvicorn
from fastapi import FastAPI
from numpy.typing import NDArray

from yardlens_api.api.deps import Container
from yardlens_api.core.config import Settings
from yardlens_api.main import create_app
from yardlens_api.services.detector import COCO_CLASSES, DetectorStatus

type Frame = NDArray[np.uint8]
type DetectionScript = Callable[[int], sv.Detections]

# Test-only credentials for the administrator the app creates on startup.
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "test-password-1234"
ORIGIN = "http://localhost:3100"
# What the dashboard sends with every request: the CSRF header from an allowed origin.
BROWSER_HEADERS = {"X-YardLens-Request": "1", "Origin": ORIGIN}


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
        self.sizes: list[int | None] = []
        self.classes: list[list[str]] = []

    @property
    def status(self) -> DetectorStatus:
        return DetectorStatus(model=self.model, device="cpu", loaded=True)

    @property
    def class_names(self) -> Sequence[str]:
        return COCO_CLASSES

    @property
    def busy_seconds(self) -> float:
        return 0.0

    def load(self, model_name: str) -> None:
        self.model = model_name

    def detect(
        self,
        frame: Frame,
        *,
        confidence: float,
        classes: Sequence[str],
        imgsz: int | None = None,
    ) -> sv.Detections:
        self.calls += 1
        self.sizes.append(imgsz)
        self.classes.append(list(classes))
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
        cors_origins=[ORIGIN],
        public_url=ORIGIN,
        admin_username=ADMIN_USERNAME,
        admin_password=ADMIN_PASSWORD,
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


async def login(http: httpx.AsyncClient, password: str = ADMIN_PASSWORD) -> httpx.Response:
    response = await http.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": password}
    )
    assert response.status_code == 200, response.text
    return response


def browser(app: FastAPI) -> httpx.AsyncClient:
    """A client that behaves like the dashboard: CSRF header, allowed origin, cookie jar."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=BROWSER_HEADERS
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Signed in as the administrator."""
    async with browser(app) as http:
        await login(http)
        yield http


@pytest.fixture
async def anon_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Not signed in."""
    async with browser(app) as http:
        yield http


@contextlib.asynccontextmanager
async def serve(app: Any) -> AsyncGenerator[str]:
    """Serve an ASGI app on a free local port (its lifespan is the caller's); yields its URL."""
    instance = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_config=None)
    )
    task = asyncio.create_task(instance.serve())
    for _ in range(500):
        if instance.started:
            break
        await asyncio.sleep(0.01)
    port: int = instance.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    instance.should_exit = True
    await task


@pytest.fixture
async def live_server(app: FastAPI) -> AsyncIterator[str]:
    """The app over a real socket (WebSockets, workers); yields its http:// address."""
    async with serve(app) as url:
        yield url
