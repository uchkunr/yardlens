"""Event persistence, querying and retention."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yardlens_api.core.enums import EventType
from yardlens_api.db.models import Camera, Event
from yardlens_api.schemas.event import EventPage, EventRead

logger = logging.getLogger(__name__)

SNAPSHOTS_URL_PREFIX = "/media/snapshots"


def snapshot_url(path: str | None) -> str | None:
    return f"{SNAPSHOTS_URL_PREFIX}/{path}" if path else None


def to_event_read(event: Event, camera_name: str) -> EventRead:
    return EventRead(
        id=event.id,
        camera_id=event.camera_id,
        camera_name=camera_name,
        type=EventType(event.type),
        class_name=event.class_name,
        tracker_id=event.tracker_id,
        confidence=event.confidence,
        snapshot_url=snapshot_url(event.snapshot_path),
        details=event.details,
        created_at=event.created_at,
    )


@dataclass(frozen=True, slots=True)
class EventFilters:
    camera_id: int | None = None
    types: Sequence[EventType] = ()
    class_name: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def _apply_filters(statement: Select[Event, str], filters: EventFilters) -> Select[Event, str]:
    if filters.camera_id is not None:
        statement = statement.where(Event.camera_id == filters.camera_id)
    if filters.types:
        statement = statement.where(
            Event.type.in_([event_type.value for event_type in filters.types])
        )
    if filters.class_name:
        statement = statement.where(Event.class_name == filters.class_name)
    if filters.since is not None:
        statement = statement.where(Event.created_at >= filters.since)
    if filters.until is not None:
        statement = statement.where(Event.created_at < filters.until)
    return statement


async def list_events(
    session: AsyncSession, filters: EventFilters, *, page: int, page_size: int
) -> EventPage:
    base = _apply_filters(select(Event, Camera.name).join(Camera), filters)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.execute(
        base.order_by(Event.created_at.desc(), Event.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [to_event_read(event, camera_name) for event, camera_name in rows]
    return EventPage(items=items, total=total or 0, page=page, page_size=page_size)


async def get_event(session: AsyncSession, event_id: int) -> EventRead | None:
    row = (
        await session.execute(select(Event, Camera.name).join(Camera).where(Event.id == event_id))
    ).first()
    if row is None:
        return None
    event, camera_name = row
    return to_event_read(event, camera_name)


async def purge_events_before(session: AsyncSession, cutoff: datetime, snapshots_dir: Path) -> int:
    """Delete events older than ``cutoff`` together with their snapshot files."""
    paths = (
        await session.scalars(
            select(Event.snapshot_path).where(
                Event.created_at < cutoff, Event.snapshot_path.is_not(None)
            )
        )
    ).all()
    result = await session.execute(delete(Event).where(Event.created_at < cutoff))
    await session.commit()
    await asyncio.to_thread(_remove_files, snapshots_dir, [path for path in paths if path])
    deleted: int = getattr(result, "rowcount", 0) or 0
    return deleted


async def delete_snapshots_for_camera(
    session: AsyncSession, camera_id: int, snapshots_dir: Path
) -> None:
    paths = (
        await session.scalars(
            select(Event.snapshot_path).where(
                Event.camera_id == camera_id, Event.snapshot_path.is_not(None)
            )
        )
    ).all()
    await remove_snapshot_files(snapshots_dir, [path for path in paths if path])


async def remove_snapshot_files(snapshots_dir: Path, relative_paths: Sequence[str]) -> None:
    await asyncio.to_thread(_remove_files, snapshots_dir, relative_paths)


def _remove_files(root: Path, relative_paths: Sequence[str]) -> None:
    parents: set[Path] = set()
    for relative in relative_paths:
        target = root / relative
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete snapshot", extra={"path": str(target)})
        parents.add(target.parent)
    for directory in sorted(parents, key=lambda path: len(path.parts), reverse=True):
        current = directory
        while current != root and root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
