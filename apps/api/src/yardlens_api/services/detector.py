"""Shared object detector.

The YOLO model is loaded once per process and shared by every camera worker. Ultralytics models
are not safe for concurrent ``predict`` calls, so inference is serialized with a lock.

Where it runs is decided by ``plan_inference``: an NVIDIA GPU (half precision) or Apple's Metal
when available, otherwise the CPU through OpenVINO, which needs about half the time and a third
less CPU per frame than PyTorch there, on Intel and AMD alike. The OpenVINO copy of a model is
converted once and cached next to the weights; until it is ready (the first start, a new custom
model) the PyTorch model serves frames, and the converted one replaces it as soon as it is done.
"""

import functools
import importlib
import json
import logging
import os
import threading
import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

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
# Ultralytics would otherwise try to pip-install whatever it finds missing at runtime.
os.environ.setdefault("YOLO_AUTOINSTALL", "false")

AVAILABLE_MODELS: tuple[str, ...] = ("yolo26n.pt", "yolo26s.pt", "yolo26m.pt", "yolo11n.pt")
DEFAULT_INPUT_SIZE = 640
_OPENVINO_STAMP = "yardlens.json"


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


class Backend(StrEnum):
    PYTORCH = "pytorch"
    OPENVINO = "openvino"


@dataclass(frozen=True, slots=True)
class DetectorStatus:
    model: str
    device: str
    loaded: bool
    backend: str = Backend.PYTORCH.value
    optimizing: bool = False
    """An OpenVINO copy of the model is being prepared; the PyTorch one serves meanwhile."""
    threads: int | None = None


class Detector(Protocol):
    @property
    def status(self) -> DetectorStatus: ...

    @property
    def class_names(self) -> Sequence[str]: ...

    @property
    def busy_seconds(self) -> float:
        """Total time spent in inference; the load governor reads its rate of change."""
        ...

    def load(self, model_name: str) -> None: ...

    def detect(
        self,
        frame: NDArray[np.uint8],
        *,
        confidence: float,
        classes: Sequence[str],
        imgsz: int | None = None,
    ) -> sv.Detections: ...


@dataclass(frozen=True, slots=True)
class InferencePlan:
    backend: Backend
    device: str
    """A torch device for PyTorch; ``intel:cpu`` or ``intel:gpu`` for OpenVINO."""

    @property
    def on_gpu(self) -> bool:
        return self.device.startswith(("cuda", "mps", "intel:gpu"))


def plan_inference(
    device: str,
    backend: str = "auto",
    *,
    cuda: bool,
    mps: bool,
    openvino: bool,
) -> InferencePlan:
    """Pick the backend and device. Integrated Intel graphics are only used when asked for
    (``intel:gpu``): measured, they save no CPU over OpenVINO on the CPU itself and take close to
    a minute to compile the model."""
    if device.startswith("intel:"):
        if openvino:
            return InferencePlan(Backend.OPENVINO, device)
        return InferencePlan(Backend.PYTORCH, "cpu")
    if device == "auto" and backend != Backend.OPENVINO:
        if cuda:
            return InferencePlan(Backend.PYTORCH, "cuda:0")
        if mps:
            return InferencePlan(Backend.PYTORCH, "mps")
    if device not in {"auto", "cpu"}:
        return InferencePlan(Backend.PYTORCH, device)
    if openvino and backend != Backend.PYTORCH:
        return InferencePlan(Backend.OPENVINO, "intel:cpu")
    return InferencePlan(Backend.PYTORCH, "cpu")


def openvino_dir(weights: Path) -> Path:
    """Where Ultralytics exports the OpenVINO copy of ``weights``."""
    return weights.with_name(f"{weights.stem}_openvino_model")


def _weights_stamp(weights: Path) -> dict[str, int]:
    stat = weights.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def openvino_ready(weights: Path) -> bool:
    """Whether a converted copy exists and was made from these exact weights."""
    stamp = openvino_dir(weights) / _OPENVINO_STAMP
    try:
        return weights.is_file() and json.loads(stamp.read_text()) == _weights_stamp(weights)
    except OSError, ValueError:
        return False


def export_openvino(weights: Path) -> Path:
    """Convert ``weights`` to OpenVINO (dynamic input size, full precision) and stamp the copy."""
    from ultralytics import YOLO  # pyright: ignore[reportMissingTypeStubs]

    started = time.monotonic()
    ultralytics_log = logging.getLogger("ultralytics")
    level = ultralytics_log.level
    # The exporter narrates every step and torch warns about tracing on Python 3.14; neither
    # is news to anyone reading the API log.
    ultralytics_log.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            exported = Path(
                str(
                    YOLO(str(weights)).export(  # pyright: ignore[reportUnknownMemberType]
                        format="openvino", dynamic=True, imgsz=DEFAULT_INPUT_SIZE, verbose=False
                    )
                )
            )
    finally:
        ultralytics_log.setLevel(level)
    (exported / _OPENVINO_STAMP).write_text(json.dumps(_weights_stamp(weights)))
    logger.info(
        "Converted model for OpenVINO",
        extra={"model": weights.name, "seconds": round(time.monotonic() - started, 1)},
    )
    return exported


def _openvino_available() -> bool:
    try:
        importlib.import_module("openvino")
    except ImportError:
        return False
    return True


def _limit_openvino_threads(model: YOLO, threads: int, directory: Path) -> None:
    """Ultralytics compiles OpenVINO models for every core; recompile with ``threads``."""
    try:
        backend: Any = cast("Any", model).predictor.model.backend
        compile_model: functools.partial[Any] = backend.compile_model
        if compile_model.keywords.get("device_name") != "CPU":
            return
        config = {**compile_model.keywords["config"], "INFERENCE_NUM_THREADS": threads}
        core: Any = cast("Any", compile_model.func).__self__
        weights = next(directory.glob("*.xml"))
        backend.compile_model = functools.partial(
            compile_model.func, device_name="CPU", config=config
        )
        backend.ov_compiled_model = backend.compile_model(core.read_model(str(weights)))
    except Exception:
        logger.warning("Could not limit OpenVINO threads; using its defaults", exc_info=True)


class YoloDetector:
    def __init__(
        self,
        *,
        models_dir: Path,
        model_name: str,
        device: str = "auto",
        backend: str = "auto",
        cpu_threads: int = 2,
    ) -> None:
        self._models_dir = models_dir
        self._model_name = model_name
        self._requested_device = device
        self._requested_backend = backend
        self._cpu_threads = cpu_threads
        self._plan = InferencePlan(Backend.PYTORCH, device)
        self._quantize: int | None = None
        self._model: YOLO | None = None
        self._names: dict[int, str] = dict(enumerate(COCO_CLASSES))
        self._lock = threading.Lock()
        self._generation = 0
        self._optimizing = False
        self._busy = 0.0

    @property
    def status(self) -> DetectorStatus:
        return DetectorStatus(
            model=self._model_name,
            device=self._plan.device,
            loaded=self._model is not None,
            backend=self._plan.backend.value,
            optimizing=self._optimizing,
            threads=self._cpu_threads,
        )

    @property
    def plan(self) -> InferencePlan:
        return self._plan

    @property
    def class_names(self) -> Sequence[str]:
        return [self._names[index] for index in sorted(self._names)]

    @property
    def busy_seconds(self) -> float:
        return self._busy

    def resolve_plan(self) -> InferencePlan:
        import torch

        return plan_inference(
            self._requested_device,
            self._requested_backend,
            cuda=torch.cuda.is_available(),
            mps=torch.backends.mps.is_available(),
            openvino=_openvino_available(),
        )

    def load(self, model_name: str) -> None:
        """Load (or swap to) a model. Blocking: call from a worker thread, not the event loop.

        Returns once a model is serving; an OpenVINO conversion may continue in the background.
        """
        import torch

        # Process-wide. Also bounds the CPU-side work (pre/post-processing) when on a GPU.
        torch.set_num_threads(self._cpu_threads)
        self._models_dir.mkdir(parents=True, exist_ok=True)
        weights = self._models_dir / model_name
        plan = self.resolve_plan()
        with self._lock:
            self._generation += 1
            generation = self._generation

        if plan.backend is Backend.OPENVINO and openvino_ready(weights):
            try:
                self._install(self._open_openvino(weights, plan), model_name, plan, generation)
            except Exception:
                logger.exception("Could not load the OpenVINO model; converting it again")
            else:
                return

        # PyTorch serves first: it loads in seconds, while a conversion can take a minute.
        serving = plan if plan.backend is Backend.PYTORCH else InferencePlan(Backend.PYTORCH, "cpu")
        self._install(self._open_pytorch(weights, serving), model_name, serving, generation)
        if plan.backend is Backend.OPENVINO:
            self._optimizing = True
            threading.Thread(
                target=self._optimize,
                args=(weights, model_name, plan, generation),
                name="model-optimizer",
                daemon=True,
            ).start()

    def _open_pytorch(self, weights: Path, plan: InferencePlan) -> YOLO:
        from ultralytics import YOLO  # pyright: ignore[reportMissingTypeStubs]

        quantize = 16 if plan.device.startswith("cuda") else None
        logger.info(
            "Loading detection model",
            extra={
                "model": weights.name,
                "backend": plan.backend.value,
                "device": plan.device,
                "precision": quantize or 32,
            },
        )
        model = YOLO(str(weights))
        # Warm up so the first real frame does not pay the graph/kernels initialisation cost.
        model.predict(  # pyright: ignore[reportUnknownMemberType]
            np.zeros((DEFAULT_INPUT_SIZE, DEFAULT_INPUT_SIZE, 3), dtype=np.uint8),
            device=plan.device,
            quantize=quantize,
            verbose=False,
        )
        return model

    def _open_openvino(self, weights: Path, plan: InferencePlan) -> YOLO:
        from ultralytics import YOLO  # pyright: ignore[reportMissingTypeStubs]

        logger.info(
            "Loading detection model",
            extra={"model": weights.name, "backend": "openvino", "device": plan.device},
        )
        directory = openvino_dir(weights)
        model = YOLO(str(directory), task="detect")
        model.predict(  # pyright: ignore[reportUnknownMemberType]
            np.zeros((DEFAULT_INPUT_SIZE, DEFAULT_INPUT_SIZE, 3), dtype=np.uint8),
            device=plan.device,
            verbose=False,
        )
        _limit_openvino_threads(model, self._cpu_threads, directory)
        return model

    def _install(self, model: YOLO, model_name: str, plan: InferencePlan, generation: int) -> None:
        names: dict[int, str] = {int(key): str(value) for key, value in model.names.items()}  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType,reportUnknownArgumentType]
        with self._lock:
            if generation != self._generation:
                return  # a newer load() won
            self._model = model
            self._model_name = model_name
            self._plan = plan
            self._quantize = 16 if plan.device.startswith("cuda") else None
            self._names = names

    def _optimize(
        self, weights: Path, model_name: str, plan: InferencePlan, generation: int
    ) -> None:
        try:
            export_openvino(weights)
            model = self._open_openvino(weights, plan)
            self._install(model, model_name, plan, generation)
            logger.info("Switched to OpenVINO", extra={"model": model_name, "device": plan.device})
        except Exception:
            logger.exception("OpenVINO conversion failed; staying on PyTorch")
        finally:
            with self._lock:
                if generation == self._generation:
                    self._optimizing = False

    def detect(
        self,
        frame: NDArray[np.uint8],
        *,
        confidence: float,
        classes: Sequence[str],
        imgsz: int | None = None,
    ) -> sv.Detections:
        with self._lock:
            model = self._model
            if model is None:
                return sv.Detections.empty()
            wanted = set(classes)
            class_ids = [index for index, name in self._names.items() if name in wanted]
            if not class_ids:
                return sv.Detections.empty()
            started = time.perf_counter()
            try:
                results = model.predict(  # pyright: ignore[reportUnknownMemberType]
                    frame,
                    conf=confidence,
                    classes=class_ids,
                    device=self._plan.device,
                    quantize=self._quantize,
                    imgsz=imgsz or DEFAULT_INPUT_SIZE,
                    verbose=False,
                )
            finally:
                self._busy += time.perf_counter() - started
        detections = sv.Detections.from_ultralytics(next(iter(results)))
        detections.data["class_name"] = np.array(
            [self._names.get(int(class_id), str(class_id)) for class_id in detections.class_id]
            if detections.class_id is not None
            else [],
            dtype=object,
        )
        return detections
