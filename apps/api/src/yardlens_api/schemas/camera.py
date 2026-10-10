from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from yardlens_api.core.enums import CameraStatus, SourceType

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
CameraName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
SourceUrl = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
InferenceFps = Annotated[float, Field(ge=0.2, le=15.0)]


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


class CameraCreate(BaseModel):
    name: CameraName
    source_type: SourceType
    url: SourceUrl
    enabled: bool = True
    inference_fps: InferenceFps = 3.0
    line: CountingLine | None = None


class CameraUpdate(BaseModel):
    """Partial update; only fields present in the request body are applied."""

    name: CameraName | None = None
    source_type: SourceType | None = None
    url: SourceUrl | None = None
    enabled: bool | None = None
    inference_fps: InferenceFps | None = None
    line: CountingLine | None = None


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


class CameraRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_type: SourceType
    url: str
    enabled: bool
    inference_fps: float
    line: CountingLine | None
    created_at: datetime
    updated_at: datetime
    runtime: CameraRuntime


class CameraTestRequest(BaseModel):
    source_type: SourceType
    url: SourceUrl
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)


class CameraTestResult(BaseModel):
    success: bool
    message: str
    resolved_url: str | None = None
    width: int | None = None
    height: int | None = None
    elapsed_ms: int
    preview: str | None = Field(default=None, description="JPEG preview as a data URL")
