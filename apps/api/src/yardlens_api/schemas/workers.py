"""Workers as the dashboard sees them, and what a worker reports about its machine."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from yardlens_api.services.governor import LoadLevelName

WorkerName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class WorkerInfo(BaseModel):
    """The worker's machine, as it reports it on connecting."""

    hostname: str = Field(max_length=255)
    platform: str = Field(max_length=255)
    version: str = Field(max_length=32)
    logical_cpus: int = Field(ge=1)
    physical_cores: int = Field(ge=1)
    cpu_limit: float | None = Field(
        default=None, description="CPUs the worker's container may use, when it is limited"
    )
    memory_total_bytes: int = Field(ge=0)
    gpu: str | None = Field(default=None, max_length=255)
    backend: str = Field(max_length=32, description="pytorch or openvino")
    device: str = Field(max_length=32)


class NodeLoad(BaseModel):
    """A node's latest resource sample."""

    cpu_percent: float
    process_cpu_percent: float
    memory_percent: float
    gpu_percent: float | None = None
    detector_busy_percent: float
    disk_free_bytes: int | None = None
    level: LoadLevelName
    model: str
    optimizing: bool = Field(default=False, description="Preparing an OpenVINO copy of the model")


class WorkerCreate(BaseModel):
    name: WorkerName


class WorkerUpdate(BaseModel):
    name: WorkerName


class WorkerRead(BaseModel):
    id: int
    name: str
    online: bool
    created_at: datetime
    last_seen_at: datetime | None = Field(description="Now while online; when it left otherwise")
    address: str | None
    version: str | None
    info: WorkerInfo | None = Field(description="None until it has connected once")
    clock_offset_seconds: float | None = Field(
        description="How far the worker's clock is behind the server's (negative: ahead); "
        "None while offline. Its times are corrected by this much."
    )
    load: NodeLoad | None
    cameras: list[int] = Field(description="Cameras this worker processes")


class WorkerCommands(BaseModel):
    """Ready-made commands that start the worker on another machine."""

    cpu: str
    gpu: str = Field(description="For machines with an NVIDIA GPU")


class WorkerCreated(BaseModel):
    worker: WorkerRead
    token: str = Field(description="Shown once: only its hash is stored")
    commands: WorkerCommands


class ClusterRead(BaseModel):
    local_processing: bool = Field(description="This server processes cameras as well")
    local_cameras: list[int] = Field(description="Cameras this server processes")
    unplaced_cameras: list[int] = Field(description="Enabled cameras no node can take right now")
    workers: list[WorkerRead]


class LocalProcessingUpdate(BaseModel):
    enabled: bool
