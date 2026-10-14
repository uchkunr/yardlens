"""Shared object detector.

The YOLO model is loaded once per process and shared by every camera worker. Ultralytics models
are not safe for concurrent ``predict`` calls, so inference is serialized with a lock. ``auto``
picks an NVIDIA GPU (in half precision) or Apple's Metal when available; on the CPU, PyTorch is
held to a few threads, because its default of one per core spends far more CPU than it saves.
"""

import logging
import os
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

# PyTorch's CPU threads (OpenMP) spin for ~200 ms after every parallel region by default. With
# inference every few tens of milliseconds they never rest, which kept every core busy; waiting
# passively costs a few milliseconds per frame. OpenMP reads this once, so it has to be set
# before torch loads (it is imported lazily below).
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

AVAILABLE_MODELS: tuple[str, ...] = ("yolo26n.pt", "yolo26s.pt", "yolo26m.pt", "yolo11n.pt")


def available_models(models_dir: Path) -> list[str]:
    """The stock models plus any custom weights (`*.pt`) dropped into `models_dir`."""
    custom = sorted(
        path.name
        for path in models_dir.glob("*.pt")
        if path.is_file() and path.name not in AVAILABLE_MODELS
    )
    return [*AVAILABLE_MODELS, *custom]


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

    if torch.cuda.is_available():
        return "cuda:0"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class YoloDetector:
    def __init__(
        self, *, models_dir: Path, model_name: str, device: str = "auto", cpu_threads: int = 4
    ) -> None:
        self._models_dir = models_dir
        self._model_name = model_name
        self._requested_device = device
        self._device = device
        self._cpu_threads = cpu_threads
        self._quantize: int | None = None
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
        import torch
        from ultralytics import YOLO  # pyright: ignore[reportMissingTypeStubs]

        # Process-wide. Also bounds the CPU-side work (pre/post-processing) when on a GPU.
        torch.set_num_threads(self._cpu_threads)
        self._models_dir.mkdir(parents=True, exist_ok=True)
        weights = self._models_dir / model_name
        device = resolve_device(self._requested_device)
        # Half precision on NVIDIA GPUs; full precision elsewhere (None).
        quantize = 16 if device.startswith("cuda") else None
        logger.info(
            "Loading detection model",
            extra={"model": model_name, "device": device, "precision": quantize or 32},
        )
        model = YOLO(str(weights))
        names: dict[int, str] = {int(key): str(value) for key, value in model.names.items()}  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType,reportUnknownArgumentType]
        # Warm up so the first real frame does not pay the graph/kernels initialisation cost.
        model.predict(  # pyright: ignore[reportUnknownMemberType]
            np.zeros((640, 640, 3), dtype=np.uint8), device=device, quantize=quantize, verbose=False
        )
        with self._lock:
            self._model = model
            self._model_name = model_name
            self._device = device
            self._quantize = quantize
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
                frame,
                conf=confidence,
                classes=class_ids,
                device=self._device,
                quantize=self._quantize,
                verbose=False,
            )
        detections = sv.Detections.from_ultralytics(next(iter(results)))
        detections.data["class_name"] = np.array(
            [self._names.get(int(class_id), str(class_id)) for class_id in detections.class_id]
            if detections.class_id is not None
            else [],
            dtype=object,
        )
        return detections
