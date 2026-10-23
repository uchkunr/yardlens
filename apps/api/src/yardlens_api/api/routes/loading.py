import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response
from pydantic import AwareDatetime

from yardlens_api.api.deps import ContainerDep, SessionDep
from yardlens_api.api.params import check_range, parse_timezone
from yardlens_api.core.errors import NotFoundError
from yardlens_api.core.i18n import Language
from yardlens_api.schemas.loading import (
    LoadingSessionPage,
    LoadingSessionRead,
    LoadingSessionUpdate,
    LoadingSummary,
)
from yardlens_api.services.event_bus import BusMessage
from yardlens_api.services.loading_store import (
    SessionFilters,
    all_sessions,
    build_workbook,
    get_session_row,
    list_sessions,
    row_to_read,
    summary,
)
from yardlens_api.services.plates import normalize_plate

router = APIRouter(prefix="/loading", tags=["loading"])

_EXPORT_LIMIT = 50_000
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/summary", response_model=LoadingSummary)
async def loading_summary(
    session: SessionDep,
    tz: str = "UTC",
    days: Annotated[int, Query(ge=1, le=92)] = 14,
) -> LoadingSummary:
    """Today's totals, vehicles in a bay right now, and one entry per day for ``days`` days."""
    return await summary(session, tz=parse_timezone(tz), days=days)


@router.get("/sessions", response_model=LoadingSessionPage)
async def get_loading_sessions(
    session: SessionDep,
    camera_id: int | None = None,
    plate: str | None = None,
    since: AwareDatetime | None = None,
    until: AwareDatetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> LoadingSessionPage:
    """Loading history, newest first. ``plate`` matches part of a plate; spaces are ignored."""
    check_range(since, until)
    filters = SessionFilters(camera_id=camera_id, plate=plate, since=since, until=until)
    return await list_sessions(session, filters, page=page, page_size=page_size)


@router.get(
    "/sessions/export",
    response_class=Response,
    responses={200: {"content": {_XLSX: {}}, "description": "Excel workbook"}},
)
async def export_loading_sessions(
    session: SessionDep,
    camera_id: int | None = None,
    plate: str | None = None,
    since: AwareDatetime | None = None,
    until: AwareDatetime | None = None,
    tz: str = "UTC",
    lang: Language = "ru",
) -> Response:
    """The same history as an Excel file, oldest first, with times in ``tz``."""
    check_range(since, until)
    zone = parse_timezone(tz)
    filters = SessionFilters(camera_id=camera_id, plate=plate, since=since, until=until)
    rows = await all_sessions(session, filters, limit=_EXPORT_LIMIT)
    # openpyxl is synchronous; a large history would otherwise stall every other request.
    content = await asyncio.to_thread(build_workbook, rows, tz=zone, language=lang)
    day = datetime.now(UTC).astimezone(zone).strftime("%Y-%m-%d")
    return Response(
        content=content,
        media_type=_XLSX,
        headers={"Content-Disposition": f'attachment; filename="yardlens-loading-{day}.xlsx"'},
    )


@router.get("/sessions/{session_id}", response_model=LoadingSessionRead)
async def get_loading_session(session_id: str, session: SessionDep) -> LoadingSessionRead:
    found = await get_session_row(session, session_id)
    if found is None:
        raise NotFoundError(f"Loading session {session_id} not found")
    return row_to_read(*found)


@router.patch("/sessions/{session_id}", response_model=LoadingSessionRead)
async def update_loading_session(
    session_id: str, payload: LoadingSessionUpdate, container: ContainerDep, session: SessionDep
) -> LoadingSessionRead:
    """Fix the plate by hand. The camera will not overwrite a corrected plate."""
    found = await get_session_row(session, session_id)
    if found is None:
        raise NotFoundError(f"Loading session {session_id} not found")
    row, camera_name = found
    row.plate = normalize_plate(payload.plate) if payload.plate else None
    row.plate_confidence = None
    row.plate_corrected = True
    await session.commit()
    read = row_to_read(row, camera_name)
    container.bus.publish(BusMessage(kind="session", data=read.model_dump_json()))
    return read
