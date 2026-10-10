from datetime import datetime

from pydantic import BaseModel


class ClassCount(BaseModel):
    class_name: str
    count: int


class CameraCount(BaseModel):
    camera_id: int
    camera_name: str
    entered: int
    line_in: int
    line_out: int


class HourlyBucket(BaseModel):
    hour: datetime
    entered: int
    line_in: int
    line_out: int


class StatsOverview(BaseModel):
    timezone: str
    vehicles_today: int
    objects_today: int
    line_in_today: int
    line_out_today: int
    events_last_hour: int
    cameras_online: int
    cameras_total: int
    per_class: list[ClassCount]
    per_camera: list[CameraCount]
    hourly: list[HourlyBucket]
