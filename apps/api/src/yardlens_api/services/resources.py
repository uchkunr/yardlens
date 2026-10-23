"""CPU budget, process priority and the resource monitor behind the load governor.

``plan_resources`` splits the machine between the libraries that start their own threads
(inference, video decoding, OpenCV, plate OCR). Left alone each assumes it owns every core, and
together they oversubscribe the CPU many times over. ``apply_process_plan`` applies the parts that
are process-wide and lowers the process priority, so the operating system, the database and the
web app stay responsive even when the cameras are busy.

``ResourceMonitor`` samples the machine every couple of seconds on its own thread, feeds the
``Governor`` and publishes the resulting ``Limits`` for the camera workers.
"""

import importlib
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import cv2
import psutil

from yardlens_api.services.governor import (
    Budget,
    Governor,
    Limits,
    LoadLevel,
    ResourceSample,
    limits_for,
)

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_SECONDS = 2.0
HISTORY_SAMPLES = 90  # three minutes
# Below this much free space snapshots are no longer written, so the database keeps room to work.
DISK_RESERVE_BYTES = 2 * 1024**3


@dataclass(frozen=True, slots=True)
class ResourcePlan:
    logical_cpus: int
    physical_cores: int
    inference_threads: int
    decode_threads: int
    """FFmpeg threads per video stream."""
    opencv_threads: int
    plate_threads: int


def container_cpu_limit(root: Path = Path("/sys/fs/cgroup")) -> float | None:
    """CPUs a container may use (``docker run --cpus``), or ``None`` when unlimited. Reads
    cgroup v2's ``cpu.max``, else v1's CFS quota."""
    try:
        quota, period = (root / "cpu.max").read_text().split()[:2]
        if quota != "max":
            return int(quota) / int(period)
    except OSError, ValueError:
        pass
    try:
        quota = (root / "cpu" / "cpu.cfs_quota_us").read_text().strip()
        period = (root / "cpu" / "cpu.cfs_period_us").read_text().strip()
        if int(quota) > 0:
            return int(quota) / int(period)
    except OSError, ValueError:
        pass
    return None


def plan_resources(
    *, inference_threads: int = 0, logical: int | None = None, physical: int | None = None
) -> ResourcePlan:
    """Thread counts for this machine; ``inference_threads`` > 0 overrides the automatic choice.

    Measured with YOLO26n on OpenVINO: two threads use ~20% less CPU per frame than four, at a
    latency (~20 ms) far below the frame interval, so inference stays narrow and the remaining
    cores are left to decoding and everything else.
    """
    logical = logical or psutil.cpu_count(logical=True) or 1
    physical = physical or psutil.cpu_count(logical=False) or logical
    return ResourcePlan(
        logical_cpus=logical,
        physical_cores=physical,
        inference_threads=inference_threads or min(4, max(1, physical // 3)),
        decode_threads=1 if physical <= 4 else 2,
        opencv_threads=1 if physical <= 4 else 2,
        plate_threads=1 if physical <= 6 else 2,
    )


def apply_process_plan(plan: ResourcePlan, *, low_priority: bool) -> None:
    """Process-wide settings. Call before worker threads start, so they inherit the priority."""
    # OpenCV parallelises resize and colour conversion over every core by default.
    cv2.setNumThreads(plan.opencv_threads)
    if not low_priority:
        return
    process = psutil.Process()
    try:
        if sys.platform == "win32":
            process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            process.nice(max(process.nice(), 5))
    except psutil.Error, OSError:
        logger.warning("Could not lower the process priority")
    else:
        logger.info("Running at reduced priority so the system stays responsive")


class GpuProbe:
    """NVIDIA GPU load through NVML. Silent no-op without an NVIDIA driver."""

    def __init__(self, index: int = 0) -> None:
        self._nvml: Any = None
        self._handle: Any = None
        self.name: str | None = None
        try:
            nvml: Any = importlib.import_module("pynvml")
            nvml.nvmlInit()
            self._handle = nvml.nvmlDeviceGetHandleByIndex(index)
            name = nvml.nvmlDeviceGetName(self._handle)
            self.name = name.decode() if isinstance(name, bytes) else str(cast("object", name))
            self._nvml = nvml
        except Exception:
            self._nvml = None

    @property
    def available(self) -> bool:
        return self._nvml is not None

    def read(self) -> tuple[float, int, int] | None:
        """Utilisation (%), memory used and total (bytes)."""
        if self._nvml is None:
            return None
        try:
            utilization = self._nvml.nvmlDeviceGetUtilizationRates(self._handle)
            memory = self._nvml.nvmlDeviceGetMemoryInfo(self._handle)
            return float(utilization.gpu), int(memory.used), int(memory.total)
        except Exception:
            return None


@dataclass(frozen=True, slots=True)
class MachineReading:
    """One sample, with the details the system page shows."""

    at: float
    cpu_percent: float
    process_cpu_percent: float
    memory_percent: float
    memory_used: int
    memory_total: int
    process_memory: int
    detector_busy: float
    gpu_percent: float | None = None
    gpu_memory_used: int | None = None
    gpu_memory_total: int | None = None
    disk_percent: float = 0.0
    disk_free: int = 0
    disk_total: int = 0


class ResourceMonitor:
    def __init__(
        self,
        *,
        plan: ResourcePlan,
        budget: Callable[[], Budget],
        detector_busy_seconds: Callable[[], float],
        inference_on_gpu: Callable[[], bool],
        data_dir: Path,
        gpu: GpuProbe | None = None,
        interval: float = SAMPLE_INTERVAL_SECONDS,
    ) -> None:
        self.plan = plan
        self.data_dir = data_dir
        self._budget = budget
        self._busy_seconds = detector_busy_seconds
        self._inference_on_gpu = inference_on_gpu
        self.gpu = gpu if gpu is not None else GpuProbe()
        self._interval = interval
        self._governor = Governor()
        self._limits = limits_for(LoadLevel.NORMAL, budget())
        self._history: deque[MachineReading] = deque(maxlen=HISTORY_SAMPLES)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="resource-monitor", daemon=True)
        self._process = psutil.Process()

    def start(self) -> None:
        # Primes the CPU counters; the first real reading is relative to this moment.
        psutil.cpu_percent(None)
        self._process.cpu_percent(None)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def limits(self) -> Limits:
        """Read by every camera worker on every tick; a plain attribute read, never blocks."""
        return self._limits

    def refresh(self) -> None:
        """Re-apply the budget now (after the settings changed) rather than at the next sample."""
        self._limits = replace(
            limits_for(self._governor.level, self._budget()),
            save_images=self._limits.save_images,
        )

    def latest(self) -> MachineReading | None:
        with self._lock:
            return self._history[-1] if self._history else None

    def history(self) -> list[MachineReading]:
        with self._lock:
            return list(self._history)

    def _run(self) -> None:
        last_busy = self._busy_seconds()
        last_at = time.monotonic()
        while not self._stop.wait(self._interval):
            try:
                now = time.monotonic()
                busy = self._busy_seconds()
                busy_share = min(1.0, max(0.0, (busy - last_busy) / max(now - last_at, 1e-3)))
                last_busy, last_at = busy, now
                reading = self._read(now, busy_share)
                budget = self._budget()
                sample = ResourceSample(
                    cpu_percent=reading.cpu_percent,
                    memory_percent=reading.memory_percent,
                    detector_busy=busy_share,
                    gpu_percent=reading.gpu_percent if self._inference_on_gpu() else None,
                )
                previous = self._governor.level
                level = self._governor.update(sample, budget.cpu_limit_percent)
                save_images = reading.disk_total == 0 or reading.disk_free >= DISK_RESERVE_BYTES
                if save_images != self._limits.save_images:
                    log = logger.info if save_images else logger.error
                    log(
                        "Snapshots resumed"
                        if save_images
                        else "Disk nearly full; snapshots paused",
                        extra={"free_bytes": reading.disk_free, "path": str(self.data_dir)},
                    )
                self._limits = replace(limits_for(level, budget), save_images=save_images)
                with self._lock:
                    self._history.append(reading)
                if level != previous:
                    log = logger.warning if level > previous else logger.info
                    log(
                        "Load level changed",
                        extra={
                            "level": level.name.lower(),
                            "cpu_percent": round(reading.cpu_percent),
                            "detector_busy": round(busy_share * 100),
                        },
                    )
            except Exception:
                logger.exception("Resource sampling failed")

    def _read(self, now: float, busy_share: float) -> MachineReading:
        memory = psutil.virtual_memory()
        process_cpu = self._process.cpu_percent(None) / max(1, self.plan.logical_cpus)
        gpu = self.gpu.read()
        try:
            disk = psutil.disk_usage(str(self.data_dir))
            disk_percent, disk_free, disk_total = (
                float(disk.percent),
                int(disk.free),
                int(disk.total),
            )
        except OSError:
            disk_percent, disk_free, disk_total = 0.0, 0, 0
        return MachineReading(
            at=now,
            cpu_percent=psutil.cpu_percent(None),
            process_cpu_percent=min(100.0, process_cpu),
            memory_percent=float(memory.percent),
            memory_used=int(memory.total - memory.available),
            memory_total=int(memory.total),
            process_memory=int(self._process.memory_info().rss),
            detector_busy=busy_share,
            gpu_percent=gpu[0] if gpu else None,
            gpu_memory_used=gpu[1] if gpu else None,
            gpu_memory_total=gpu[2] if gpu else None,
            disk_percent=disk_percent,
            disk_free=disk_free,
            disk_total=disk_total,
        )
