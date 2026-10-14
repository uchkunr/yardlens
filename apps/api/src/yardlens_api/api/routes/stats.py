from fastapi import APIRouter

from yardlens_api.api.deps import ContainerDep, SessionDep
from yardlens_api.api.params import parse_timezone
from yardlens_api.schemas.stats import StatsOverview
from yardlens_api.services.stats import overview

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverview)
async def stats_overview(
    container: ContainerDep, session: SessionDep, tz: str = "UTC"
) -> StatsOverview:
    """Dashboard KPIs. ``tz`` is an IANA timezone used to define "today" and hour buckets."""
    zone = parse_timezone(tz)
    return await overview(session, tz=zone, cameras_online=container.cameras.online_count())
