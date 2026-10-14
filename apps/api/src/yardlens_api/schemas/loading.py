from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from yardlens_api.core.enums import SessionStatus


class LoadingSessionRead(BaseModel):
    id: str
    camera_id: int
    camera_name: str
    status: SessionStatus
    arrived_at: datetime = Field(description="When the vehicle pulled into the bay")
    departed_at: datetime | None = Field(description="When it left; null while it is still there")
    loading_started_at: datetime | None = Field(description="First cargo item counted")
    loading_finished_at: datetime | None = Field(description="Last cargo item counted")
    duration_seconds: int = Field(description="Time in the bay so far, or in total once it left")
    loaded: int = Field(description="Cargo items counted in the loading direction")
    unloaded: int = Field(description="Cargo items counted in the opposite direction")
    vehicle_class: str | None
    plate: str | None = Field(description="Normalized plate, e.g. '01A123BC'")
    plate_confidence: float | None
    plate_corrected: bool = Field(description="The plate was entered or fixed by a person")
    vehicle_snapshot_url: str | None
    plate_snapshot_url: str | None


class LoadingTotals(BaseModel):
    vehicles: int
    loaded: int
    unloaded: int
    average_duration_seconds: int | None = Field(
        description="Mean time in the bay of the vehicles that have left"
    )


class LoadingSessionPage(BaseModel):
    items: list[LoadingSessionRead]
    total: int
    page: int
    page_size: int
    totals: LoadingTotals = Field(description="Across every session matching the filters")


class DailyLoading(BaseModel):
    day: date
    vehicles: int
    loaded: int


class LoadingSummary(BaseModel):
    timezone: str
    loading_cameras: int = Field(description="Cameras set up as loading bays")
    today: LoadingTotals
    active: list[LoadingSessionRead] = Field(description="Vehicles in a bay right now")
    daily: list[DailyLoading] = Field(description="One entry per day, oldest first, ending today")


PlateText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=16)]


class LoadingSessionUpdate(BaseModel):
    plate: PlateText | None = Field(
        default=None, description="Correct the plate by hand; empty or null clears it"
    )
