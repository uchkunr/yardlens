from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, cast

import httpx
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from yardlens_api.core.config import Settings
from yardlens_api.services.camera_manager import CameraManager
from yardlens_api.services.detector import Detector
from yardlens_api.services.event_bus import EventBus
from yardlens_api.services.event_recorder import EventRecorder
from yardlens_api.services.notifier import TelegramNotifier
from yardlens_api.services.settings_store import SettingsStore


@dataclass(slots=True)
class Container:
    """Application-scoped services, created in the lifespan and stored on ``app.state``."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    detector: Detector
    settings_store: SettingsStore
    cameras: CameraManager
    bus: EventBus
    recorder: EventRecorder
    notifier: TelegramNotifier
    http_client: httpx.AsyncClient
    started_at: datetime


def get_container(request: Request) -> Container:
    return cast("Container", request.app.state.container)


async def get_session(
    container: Annotated[Container, Depends(get_container)],
) -> AsyncIterator[AsyncSession]:
    async with container.session_factory() as session:
        yield session


ContainerDep = Annotated[Container, Depends(get_container)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
