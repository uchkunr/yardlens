"""Shared object detector.

The YOLO model is loaded once per process and shared by every camera worker. Ultralytics models
are not safe for concurrent ``predict`` calls, so inference is serialized with a lock; a nano model
handles several cameras at a few FPS each comfortably on CPU.
"""

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np
import supervision as sv
from numpy.typing import NDArray

if TYPE_CHECKING:
    from ultralytics import YOLO  # pyright: ignore[reportMissingTypeStubs]

logger = logging.getLogger(__name__)

AVAILABLE_MODELS: tuple[str, ...] = ("yolo26n.pt", "yolo26s.pt", "yolo26m.pt", "yolo11n.pt")

COCO_CLASSES: tuple[str, ...] = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class DetectorStatus:
    model: str
    device: str
    loaded: bool


class Detector(Protocol):
    @property
    def status(self) -> DetectorStatus: ...

    @property
    def class_names(self) -> Sequence[str]: ...

    def load(self, model_name: str) -> None: ...

    def detect(
        self, frame: NDArray[np.uint8], *, confidence: float, classes: Sequence[str]
    ) -> sv.Detections: ...


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    return "cuda:0" if torch.cuda.is_available() else "cpu"


class YoloDetector:
    def __init__(self, *, models_dir: Path, model_name: str, device: str = "auto") -> None:
        self._models_dir = models_dir
        self._model_name = model_name
        self._requested_device = device
        self._device = device
        self._model: YOLO | None = None
        self._names: dict[int, str] = dict(enumerate(COCO_CLASSES))
        self._lock = threading.Lock()

    @property
    def status(self) -> DetectorStatus:
        return DetectorStatus(
            model=self._model_name, device=self._device, loaded=self._model is not None
        )

    @property
    def class_names(self) -> Sequence[str]:
        return [self._names[index] for index in sorted(self._names)]

    def load(self, model_name: str) -> None:
        """Load (or swap to) a model. Blocking: call from a worker thread, not the event loop."""
        from ultralytics import YOLO  # pyright: ignore[reportMissingTypeStubs]

        self._models_dir.mkdir(parents=True, exist_ok=True)
        weights = self._models_dir / model_name
        device = resolve_device(self._requested_device)
        logger.info("Loading detection model", extra={"model": model_name, "device": device})
        model = YOLO(str(weights))
        names: dict[int, str] = {int(key): str(value) for key, value in model.names.items()}  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType,reportUnknownArgumentType]
        # Warm up so the first real frame does not pay the graph/kernels initialisation cost.
        model.predict(np.zeros((640, 640, 3), dtype=np.uint8), device=device, verbose=False)  # pyright: ignore[reportUnknownMemberType]
        with self._lock:
            self._model = model
            self._model_name = model_name
            self._device = device
            self._names = names

    def detect(
        self, frame: NDArray[np.uint8], *, confidence: float, classes: Sequence[str]
    ) -> sv.Detections:
        with self._lock:
            model = self._model
            if model is None:
                return sv.Detections.empty()
            wanted = set(classes)
            class_ids = [index for index, name in self._names.items() if name in wanted]
            if not class_ids:
                return sv.Detections.empty()
            results = model.predict(  # pyright: ignore[reportUnknownMemberType]
                frame, conf=confidence, classes=class_ids, device=self._device, verbose=False
            )
        detections = sv.Detections.from_ultralytics(next(iter(results)))
        detections.data["class_name"] = np.array(
            [self._names.get(int(class_id), str(class_id)) for class_id in detections.class_id]
            if detections.class_id is not None
            else [],
            dtype=object,
        )
        return detections
