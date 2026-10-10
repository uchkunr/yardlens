import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from yardlens_api.api.deps import ContainerDep, SessionDep
from yardlens_api.core.enums import EventType
from yardlens_api.core.errors import AppError, NotFoundError
from yardlens_api.schemas.event import EventPage, EventRead
from yardlens_api.services.events import EventFilters, get_event, list_events

router = APIRouter(prefix="/events", tags=["events"])


class InvalidRangeError(AppError):
    status_code = 422


@router.get("", response_model=EventPage)
async def get_events(
    session: SessionDep,
    camera_id: int | None = None,
    type: Annotated[list[EventType] | None, Query()] = None,  # noqa: A002 - public query name
    class_name: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> EventPage:
    if since is not None and until is not None and since >= until:
        raise InvalidRangeError("'since' must be earlier than 'until'")
    filters = EventFilters(
        camera_id=camera_id,
        types=tuple(type or ()),
        class_name=class_name,
        since=since,
        until=until,
    )
    return await list_events(session, filters, page=page, page_size=page_size)


@router.get(
    "/stream",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_events(request: Request, container: ContainerDep) -> EventSourceResponse:
    """Server-sent events: ``event`` (a new EventRead) and ``camera_status`` messages."""

    async def messages() -> AsyncIterator[ServerSentEvent]:
        async with container.bus.subscribe() as queue:
            yield ServerSentEvent(event="ready", data="{}")
            while not await request.is_disconnected():
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=5.0)
                except TimeoutError:
                    continue
                yield ServerSentEvent(event=message.kind, data=message.data)

    return EventSourceResponse(messages(), ping=15, headers={"X-Accel-Buffering": "no"})


@router.get("/{event_id}", response_model=EventRead)
async def get_event_by_id(event_id: int, session: SessionDep) -> EventRead:
    event = await get_event(session, event_id)
    if event is None:
        raise NotFoundError(f"Event {event_id} not found")
    return event
