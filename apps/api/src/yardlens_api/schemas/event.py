from datetime import datetime
from typing import Any

from pydantic import BaseModel

from yardlens_api.core.enums import EventType


class EventRead(BaseModel):
    id: int
    camera_id: int
    camera_name: str
    type: EventType
    class_name: str | None
    tracker_id: int | None
    confidence: float | None
    snapshot_url: str | None
    details: dict[str, Any] | None
    created_at: datetime


class EventPage(BaseModel):
    items: list[EventRead]
    total: int
    page: int
    page_size: int
