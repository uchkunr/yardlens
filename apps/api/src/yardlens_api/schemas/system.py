from datetime import datetime

from pydantic import BaseModel, Field

from yardlens_api.core.enums import Activity
from yardlens_api.services.governor import LoadLevelName


class CpuInfo(BaseModel):
    percent: float = Field(description="Whole machine")
    process_percent: float = Field(description="YardLens's share of the whole machine")
    logical_cpus: int
    physical_cores: int


class MemoryInfo(BaseModel):
    percent: float
    used_bytes: int
    total_bytes: int
    process_bytes: int = Field(description="Resident memory of the YardLens API")


class GpuInfo(BaseModel):
    name: str | None
    percent: float | None
    memory_used_bytes: int | None
    memory_total_bytes: int | None


class DiskInfo(BaseModel):
    """The disk holding the data directory (snapshots, models, videos)."""

    percent: float
    free_bytes: int
    total_bytes: int
    images_paused: bool = Field(description="Snapshots are not saved: too little free space")


class InferenceInfo(BaseModel):
    model: str
    backend: str = Field(description="pytorch or openvino")
    device: str
    threads: int | None
    optimizing: bool = Field(description="An OpenVINO copy of the model is being prepared")
    busy_percent: float = Field(description="Share of time the detector spends on inference")
    models: list[str] = Field(description="Every model loaded: the general one first")


class LoadInfo(BaseModel):
    level: LoadLevelName
    cpu_limit_percent: int
    adaptive: bool
    fps_scale: float = Field(description="Applied to each camera's maximum inference rate")
    idle_fps: float
    stream_fps: float = Field(description="Live view frame rate allowed now")


class CameraLoad(BaseModel):
    id: int
    name: str
    activity: Activity
    target_fps: float
    measured_fps: float
    max_fps: float
    hardware_decoding: bool
    worker_id: int | None = Field(default=None, description="The worker it runs on; None: here")
    worker_name: str | None = None


class ResourcePoint(BaseModel):
    at: datetime
    cpu_percent: float
    process_cpu_percent: float
    memory_percent: float
    gpu_percent: float | None


class SystemResources(BaseModel):
    sampled_at: datetime | None = Field(description="None until the first sample (2 s)")
    cpu: CpuInfo
    memory: MemoryInfo
    disk: DiskInfo | None = Field(description="None until the first sample")
    gpu: GpuInfo | None
    inference: InferenceInfo
    load: LoadInfo
    cameras: list[CameraLoad]
    history: list[ResourcePoint] = Field(description="The last few minutes, oldest first")
