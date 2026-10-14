import time
from datetime import UTC, datetime, timedelta

import psutil
from fastapi import APIRouter

from yardlens_api.api.deps import ContainerDep
from yardlens_api.schemas.system import (
    CameraLoad,
    CpuInfo,
    DiskInfo,
    GpuInfo,
    InferenceInfo,
    LoadInfo,
    MemoryInfo,
    ResourcePoint,
    SystemResources,
)
from yardlens_api.services.governor import LEVEL_NAMES

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/resources", response_model=SystemResources)
async def resources(container: ContainerDep) -> SystemResources:
    """Machine load, what the governor allows right now and how fast each camera runs."""
    monitor = container.monitor
    plan = monitor.plan
    now_wall, now_mono = datetime.now(UTC), time.monotonic()

    def wall(at: float) -> datetime:
        return now_wall - timedelta(seconds=max(0.0, now_mono - at))

    latest = monitor.latest()
    if latest is None:
        memory = psutil.virtual_memory()
        cpu = CpuInfo(
            percent=0.0,
            process_percent=0.0,
            logical_cpus=plan.logical_cpus,
            physical_cores=plan.physical_cores,
        )
        mem = MemoryInfo(
            percent=float(memory.percent),
            used_bytes=int(memory.total - memory.available),
            total_bytes=int(memory.total),
            process_bytes=int(psutil.Process().memory_info().rss),
        )
    else:
        cpu = CpuInfo(
            percent=round(latest.cpu_percent, 1),
            process_percent=round(latest.process_cpu_percent, 1),
            logical_cpus=plan.logical_cpus,
            physical_cores=plan.physical_cores,
        )
        mem = MemoryInfo(
            percent=round(latest.memory_percent, 1),
            used_bytes=latest.memory_used,
            total_bytes=latest.memory_total,
            process_bytes=latest.process_memory,
        )

    gpu = (
        GpuInfo(
            name=monitor.gpu.name,
            percent=latest.gpu_percent if latest else None,
            memory_used_bytes=latest.gpu_memory_used if latest else None,
            memory_total_bytes=latest.gpu_memory_total if latest else None,
        )
        if monitor.gpu.available
        else None
    )

    detector = container.detector.status
    limits = monitor.limits()
    performance = container.settings_store.performance()
    cameras: list[CameraLoad] = []
    for worker in container.cameras.workers():
        state = worker.state()
        cameras.append(
            CameraLoad(
                id=worker.config.id,
                name=worker.config.name,
                activity=state.activity,
                target_fps=state.target_fps,
                measured_fps=state.measured_fps,
                max_fps=worker.config.inference_fps,
                hardware_decoding=state.hardware_decoding,
            )
        )
    return SystemResources(
        sampled_at=wall(latest.at) if latest else None,
        cpu=cpu,
        memory=mem,
        disk=DiskInfo(
            percent=round(latest.disk_percent, 1),
            free_bytes=latest.disk_free,
            total_bytes=latest.disk_total,
            images_paused=not limits.save_images,
        )
        if latest and latest.disk_total
        else None,
        gpu=gpu,
        inference=InferenceInfo(
            model=detector.model,
            backend=detector.backend,
            device=detector.device,
            threads=detector.threads,
            optimizing=detector.optimizing,
            busy_percent=round(latest.detector_busy * 100, 1) if latest else 0.0,
            models=[detector.model, *container.models.models],
        ),
        load=LoadInfo(
            level=LEVEL_NAMES[limits.level],
            cpu_limit_percent=performance.cpu_limit_percent,
            adaptive=limits.adaptive,
            fps_scale=limits.fps_scale,
            idle_fps=limits.idle_fps,
            stream_fps=limits.stream_fps,
        ),
        cameras=sorted(cameras, key=lambda camera: camera.id),
        history=[
            ResourcePoint(
                at=wall(point.at),
                cpu_percent=round(point.cpu_percent, 1),
                process_cpu_percent=round(point.process_cpu_percent, 1),
                memory_percent=round(point.memory_percent, 1),
                gpu_percent=point.gpu_percent,
            )
            for point in monitor.history()
        ],
    )
