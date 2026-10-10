from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DetectorInfo(BaseModel):
    model: str
    device: str
    loaded: bool


class HealthRead(BaseModel):
    status: Literal["ok"]
    version: str
    python_version: str
    started_at: datetime
    uptime_seconds: float
    cameras_total: int
    cameras_online: int
    detector: DetectorInfo
