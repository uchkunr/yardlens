from datetime import datetime

from pydantic import BaseModel, Field


class CameraCount(BaseModel):
    camera_id: int
    camera_name: str
    line_in: int
    line_out: int


class HourlyBucket(BaseModel):
    hour: datetime
    line_in: int
    line_out: int


class StatsOverview(BaseModel):
    timezone: str
    line_in_today: int = Field(description="Line crossings in, across every camera")
    line_out_today: int
    events_last_hour: int
    cameras_online: int
    cameras_total: int
    per_camera: list[CameraCount]
    hourly: list[HourlyBucket] = Field(description="The last 24 hours, oldest first")
