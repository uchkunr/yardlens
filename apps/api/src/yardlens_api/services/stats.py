"""Aggregations for the overview dashboard. Kept dialect-neutral (SQLite and Postgres)."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yardlens_api.core.enums import VEHICLE_CLASSES, EventType
from yardlens_api.db.models import Camera, Event
from yardlens_api.schemas.stats import CameraCount, ClassCount, HourlyBucket, StatsOverview

_COUNTED_TYPES = (
    EventType.OBJECT_ENTERED.value,
    EventType.LINE_CROSSED_IN.value,
    EventType.LINE_CROSSED_OUT.value,
)


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

    by_type_class = await session.execute(
        select(Event.type, Event.class_name, func.count())
        .where(Event.created_at >= day_start, Event.type.in_(_COUNTED_TYPES))
        .group_by(Event.type, Event.class_name)
    )
    entered_by_class: Counter[str] = Counter()
    line_in = line_out = 0
    for event_type, class_name, count in by_type_class:
        if event_type == EventType.OBJECT_ENTERED and class_name:
            entered_by_class[class_name] += count
        elif event_type == EventType.LINE_CROSSED_IN:
            line_in += count
        elif event_type == EventType.LINE_CROSSED_OUT:
            line_out += count

    cameras = (await session.execute(select(Camera.id, Camera.name).order_by(Camera.id))).all()
    per_camera_rows = await session.execute(
        select(Event.camera_id, Event.type, func.count())
        .where(Event.created_at >= day_start, Event.type.in_(_COUNTED_TYPES))
        .group_by(Event.camera_id, Event.type)
    )
    per_camera_counts: dict[int, Counter[str]] = {}
    for camera_id, event_type, count in per_camera_rows:
        per_camera_counts.setdefault(camera_id, Counter())[event_type] += count
    per_camera = [
        CameraCount(
            camera_id=camera_id,
            camera_name=name,
            entered=per_camera_counts.get(camera_id, Counter())[EventType.OBJECT_ENTERED.value],
            line_in=per_camera_counts.get(camera_id, Counter())[EventType.LINE_CROSSED_IN.value],
            line_out=per_camera_counts.get(camera_id, Counter())[EventType.LINE_CROSSED_OUT.value],
        )
        for camera_id, name in cameras
    ]

    timeline = await session.execute(
        select(Event.created_at, Event.type).where(
            Event.created_at >= window_start, Event.type.in_(_COUNTED_TYPES)
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
            entered=counts[EventType.OBJECT_ENTERED.value],
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
        vehicles_today=sum(
            count for name, count in entered_by_class.items() if name in VEHICLE_CLASSES
        ),
        objects_today=sum(entered_by_class.values()),
        line_in_today=line_in,
        line_out_today=line_out,
        events_last_hour=events_last_hour or 0,
        cameras_online=cameras_online,
        cameras_total=len(per_camera),
        per_class=[
            ClassCount(class_name=name, count=count)
            for name, count in entered_by_class.most_common()
        ],
        per_camera=per_camera,
        hourly=hourly,
    )
