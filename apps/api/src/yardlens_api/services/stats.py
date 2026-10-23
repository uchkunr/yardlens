"""Aggregations for the overview dashboard. Kept dialect-neutral (SQLite and Postgres)."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yardlens_api.core.enums import EventType
from yardlens_api.db.models import Camera, Event
from yardlens_api.schemas.stats import CameraCount, HourlyBucket, StatsOverview

_CROSSINGS = (EventType.LINE_CROSSED_IN.value, EventType.LINE_CROSSED_OUT.value)


def start_of_day(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def overview(
    session: AsyncSession,
    *,
    tz: ZoneInfo,
    cameras_online: int,
    now: datetime | None = None,
) -> StatsOverview:
    now = now or datetime.now(UTC)
    day_start = start_of_day(now, tz)
    window_start = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)

    cameras = (await session.execute(select(Camera.id, Camera.name).order_by(Camera.id))).all()
    per_camera_rows = await session.execute(
        select(Event.camera_id, Event.type, func.count())
        .where(Event.created_at >= day_start, Event.type.in_(_CROSSINGS))
        .group_by(Event.camera_id, Event.type)
    )
    per_camera_counts: dict[int, Counter[str]] = {}
    for camera_id, event_type, count in per_camera_rows:
        per_camera_counts.setdefault(camera_id, Counter())[event_type] += count
    per_camera = [
        CameraCount(
            camera_id=camera_id,
            camera_name=name,
            line_in=per_camera_counts.get(camera_id, Counter())[EventType.LINE_CROSSED_IN.value],
            line_out=per_camera_counts.get(camera_id, Counter())[EventType.LINE_CROSSED_OUT.value],
        )
        for camera_id, name in cameras
    ]

    timeline = await session.execute(
        select(Event.created_at, Event.type).where(
            Event.created_at >= window_start, Event.type.in_(_CROSSINGS)
        )
    )
    buckets = {window_start + timedelta(hours=offset): Counter[str]() for offset in range(24)}
    for created_at, event_type in timeline:
        hour = created_at.replace(minute=0, second=0, microsecond=0)
        if hour in buckets:
            buckets[hour][event_type] += 1
    hourly = [
        HourlyBucket(
            hour=hour.astimezone(tz),
            line_in=counts[EventType.LINE_CROSSED_IN.value],
            line_out=counts[EventType.LINE_CROSSED_OUT.value],
        )
        for hour, counts in sorted(buckets.items())
    ]

    events_last_hour = await session.scalar(
        select(func.count()).select_from(Event).where(Event.created_at >= now - timedelta(hours=1))
    )

    return StatsOverview(
        timezone=tz.key,
        line_in_today=sum(camera.line_in for camera in per_camera),
        line_out_today=sum(camera.line_out for camera in per_camera),
        events_last_hour=events_last_hour or 0,
        cameras_online=cameras_online,
        cameras_total=len(per_camera),
        per_camera=per_camera,
        hourly=hourly,
    )
