from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, status

from yardlens_api.api.deps import ContainerDep, SessionDep
from yardlens_api.core.errors import AppError
from yardlens_api.schemas.stats import StatsOverview
from yardlens_api.services.stats import overview

router = APIRouter(prefix="/stats", tags=["stats"])


class InvalidTimezoneError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


@router.get("/overview", response_model=StatsOverview)
async def stats_overview(
    container: ContainerDep, session: SessionDep, tz: str = "UTC"
) -> StatsOverview:
    """Dashboard KPIs. ``tz`` is an IANA timezone used to define "today" and hour buckets."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezoneError(f"Unknown timezone '{tz}'") from exc
    return await overview(session, tz=zone, cameras_online=container.cameras.online_count())
