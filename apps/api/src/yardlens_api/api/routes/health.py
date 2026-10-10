import platform
from datetime import UTC, datetime
from importlib.metadata import version

from fastapi import APIRouter
from sqlalchemy import func, select

from yardlens_api.api.deps import ContainerDep, SessionDep
from yardlens_api.db.models import Camera
from yardlens_api.schemas.health import DetectorInfo, HealthRead

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthRead)
async def health(container: ContainerDep, session: SessionDep) -> HealthRead:
    cameras_total = await session.scalar(select(func.count()).select_from(Camera)) or 0
    detector = container.detector.status
    now = datetime.now(UTC)
    return HealthRead(
        status="ok",
        version=version("yardlens-api"),
        python_version=platform.python_version(),
        started_at=container.started_at,
        uptime_seconds=round((now - container.started_at).total_seconds(), 1),
        cameras_total=cameras_total,
        cameras_online=container.cameras.online_count(),
        detector=DetectorInfo(model=detector.model, device=detector.device, loaded=detector.loaded),
    )
