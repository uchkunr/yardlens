from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from yardlens_api.core.enums import Activity, CameraStatus, SourceType
from yardlens_api.schemas.loading import LoadingSessionRead
from yardlens_api.schemas.settings import CameraDetection

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
CameraName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
SourceUrl = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
InferenceFps = Annotated[float, Field(ge=0.2, le=15.0)]
PlateUrl = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=2048),
    Field(
        description="High-resolution main stream (RTSP), opened only to read number plates "
        "while `url` is the camera's light sub-stream"
    ),
]


class CountingLine(BaseModel):
    """A counting line defined by two points in normalized (0-1) frame coordinates."""

    x1: Unit
    y1: Unit
    x2: Unit
    y2: Unit

    @model_validator(mode="after")
    def _distinct_points(self) -> Self:
        if abs(self.x1 - self.x2) < 1e-3 and abs(self.y1 - self.y2) < 1e-3:
            raise ValueError("Counting line points must be distinct")
        return self


class ZonePoint(BaseModel):
    x: Unit
    y: Unit


class LoadingZone(BaseModel):
    """Where a vehicle parks while it is loaded, as a polygon in normalized frame coordinates.

    A camera with a zone is a loading bay: vehicles parked in it open a loading session and cargo
    crossing the counting line is added to that session.
    """

    points: Annotated[list[ZonePoint], Field(min_length=3, max_length=24)]


WorkerPin = Annotated[
    int | None,
    Field(
        description="Process the camera on this worker only; null places it automatically "
        "(video files always run on the server)"
    ),
]


class CameraNode(BaseModel):
    """Where a camera is processed right now."""

    worker_id: int | None = Field(description="None: this server")
    worker_name: str | None = None


class CameraCreate(BaseModel):
    name: CameraName
    source_type: SourceType
    url: SourceUrl
    enabled: bool = True
    inference_fps: InferenceFps = 3.0
    line: CountingLine | None = None
    zone: LoadingZone | None = None
    plate_url: PlateUrl = None
    detection: CameraDetection | None = None
    worker_id: WorkerPin = None


class CameraUpdate(BaseModel):
    """Partial update; only fields present in the request body are applied."""

    name: CameraName | None = None
    source_type: SourceType | None = None
    url: SourceUrl | None = None
    enabled: bool | None = None
    inference_fps: InferenceFps | None = None
    line: CountingLine | None = None
    zone: LoadingZone | None = None
    plate_url: PlateUrl = None
    detection: CameraDetection | None = Field(
        default=None, description="The camera's own detection settings; null follows the general"
    )
    worker_id: WorkerPin = None


class CameraRuntime(BaseModel):
    status: CameraStatus
    last_error: str | None
    last_frame_at: datetime | None
    measured_fps: float
    frame_width: int | None
    frame_height: int | None
    visible: dict[str, int] = Field(description="Objects currently visible, by class")
    line_in: int = Field(description="Line crossings (in) since the worker started")
    line_out: int = Field(description="Line crossings (out) since the worker started")
    loading: LoadingSessionRead | None = Field(
        default=None, description="The loading session in progress, for loading-bay cameras"
    )
    activity: Activity = Field(
        default=Activity.ACTIVE,
        description="active: something moves, full rate; standby: a vehicle stands in the bay; "
        "idle: nothing moves, a trickle of frames",
    )
    target_fps: float = Field(default=0.0, description="Inference rate the camera runs at now")
    hardware_decoding: bool = Field(default=False, description="Video is decoded on the GPU")
    plate_stream_open: bool = Field(
        default=False, description="The main stream is open to read a plate"
    )


class CameraRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_type: SourceType
    url: str
    enabled: bool
    inference_fps: float
    line: CountingLine | None
    zone: LoadingZone | None
    plate_url: str | None
    detection: CameraDetection | None
    worker_id: int | None = Field(description="The worker it is pinned to; null: automatic")
    node: CameraNode | None = Field(
        description="Where it runs now; null: nowhere (disabled, or waiting for a node)"
    )
    created_at: datetime
    updated_at: datetime
    runtime: CameraRuntime


class CameraTestRequest(BaseModel):
    source_type: SourceType
    url: SourceUrl
    camera_id: int | None = Field(
        default=None,
        description="The camera being edited: its stored password replaces a masked one in url",
    )
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)


class CameraTestResult(BaseModel):
    success: bool
    message: str
    resolved_url: str | None = None
    width: int | None = None
    height: int | None = None
    elapsed_ms: int
    preview: str | None = Field(default=None, description="JPEG preview as a data URL")
