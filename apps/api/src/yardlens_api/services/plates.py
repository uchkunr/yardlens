"""Number plates: reading them with fast-alpr and settling on one answer per vehicle.

A parked vehicle is read many times. Single readings are noisy (a character lost to glare, a
smudge read as a letter), so readings are pooled per vehicle and the plate read most often and
most confidently wins. Readings in an Uzbek plate format weigh more than odd-shaped ones, but
other formats (trucks from neighbouring countries) are still accepted.
"""

import importlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

type Frame = NDArray[np.uint8]

# "01 A 123 BC" (private vehicles) and "01 123 ABC" (organisations).
_UZBEK_FORMATS = (
    re.compile(r"^(\d{2})([A-Z])(\d{3})([A-Z]{2})$"),
    re.compile(r"^(\d{2})(\d{3})([A-Z]{3})$"),
)
MIN_PLATE_LENGTH = 5
_UZBEK_WEIGHT = 1.5
# Stop reading once the leader has this many readings and most of the weight.
_SETTLED_READS = 5
_SETTLED_SHARE = 0.6
_RETRY_LOAD_AFTER_SECONDS = 600.0


def normalize_plate(text: str) -> str:
    """Upper-case letters and digits only: ' 01 a 123-bc ' → '01A123BC'."""
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def is_uzbek_plate(plate: str) -> bool:
    return any(pattern.match(plate) for pattern in _UZBEK_FORMATS)


def format_plate(plate: str) -> str:
    """'01A123BC' → '01 A 123 BC'. Plates in other formats come back unchanged."""
    for pattern in _UZBEK_FORMATS:
        match = pattern.match(plate)
        if match:
            return " ".join(match.groups())
    return plate


@dataclass(frozen=True, slots=True)
class PlateRead:
    text: str
    confidence: float
    crop: Frame


class PlateReader(Protocol):
    def read(self, image: Frame) -> list[PlateRead]: ...


class PlateVotes:
    """Pools the readings of one vehicle's plate."""

    def __init__(self) -> None:
        self._weight: dict[str, float] = {}
        self._reads: dict[str, list[float]] = {}
        self._crop: dict[str, tuple[float, Frame]] = {}

    def add(self, read: PlateRead) -> bool:
        """Count one reading. Returns whether the leading plate changed."""
        text = normalize_plate(read.text)
        if len(text) < MIN_PLATE_LENGTH:
            return False
        before = self.best
        weight = read.confidence * (_UZBEK_WEIGHT if is_uzbek_plate(text) else 1.0)
        self._weight[text] = self._weight.get(text, 0.0) + weight
        self._reads.setdefault(text, []).append(read.confidence)
        best_crop = self._crop.get(text)
        if best_crop is None or read.confidence > best_crop[0]:
            self._crop[text] = (read.confidence, read.crop)
        return self.best != before

    @property
    def best(self) -> str | None:
        if not self._weight:
            return None
        return max(self._weight, key=lambda text: self._weight[text])

    @property
    def confidence(self) -> float | None:
        best = self.best
        if best is None:
            return None
        reads = self._reads[best]
        return round(sum(reads) / len(reads), 3)

    @property
    def crop(self) -> Frame | None:
        best = self.best
        return self._crop[best][1] if best is not None else None

    @property
    def settled(self) -> bool:
        best = self.best
        if best is None:
            return False
        share = self._weight[best] / sum(self._weight.values())
        return len(self._reads[best]) >= _SETTLED_READS and share >= _SETTLED_SHARE


class FastAlprReader:
    """fast-alpr: a YOLOv9 plate detector and a CCT OCR model, both ONNX on the CPU.

    Loaded on first use (the models, a few MB, are downloaded once). Readings run a couple of
    times a second while a vehicle is parked, so two threads that sleep between runs are plenty.
    """

    def __init__(self, *, threads: int = 2) -> None:
        self._threads = threads
        self._alpr: Any = None
        self._lock = threading.Lock()
        self._failed_at: float | None = None

    def read(self, image: Frame) -> list[PlateRead]:
        with self._lock:
            alpr = self._load()
            if alpr is None:
                return []
            results: list[Any] = alpr.predict(image)
        reads: list[PlateRead] = []
        for result in results:
            ocr = result.ocr
            if ocr is None or not ocr.text:
                continue
            raw: float | list[float] = ocr.confidence
            confidence = float(np.mean(raw)) if isinstance(raw, list) else float(raw)
            box = result.detection.bounding_box
            crop = image[max(box.y1, 0) : box.y2, max(box.x1, 0) : box.x2].copy()
            if crop.size == 0:
                continue
            reads.append(
                PlateRead(text=normalize_plate(str(ocr.text)), confidence=confidence, crop=crop)
            )
        return reads

    def _load(self) -> Any:
        if self._alpr is not None:
            return self._alpr
        if (
            self._failed_at is not None
            and time.monotonic() - self._failed_at < _RETRY_LOAD_AFTER_SECONDS
        ):
            return None
        try:
            # onnxruntime ships without type information.
            ort: Any = importlib.import_module("onnxruntime")
            from fast_alpr import ALPR  # pyright: ignore[reportMissingTypeStubs]

            def options() -> Any:
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = self._threads
                opts.inter_op_num_threads = 1
                # Threads that spin between runs would keep CPU cores busy for nothing.
                opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
                return opts

            logger.info("Loading number plate models")
            self._alpr = ALPR(
                detector_model="yolo-v9-t-384-license-plate-end2end",
                detector_providers=["CPUExecutionProvider"],
                detector_sess_options=options(),
                ocr_model="cct-s-v2-global-model",
                ocr_device="cpu",
                ocr_providers=["CPUExecutionProvider"],
                ocr_sess_options=options(),
            )
        except Exception:
            logger.exception("Could not load the number plate models; retrying later")
            self._failed_at = time.monotonic()
            return None
        return self._alpr
