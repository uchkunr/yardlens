"""Runtime-editable settings persisted in the ``app_settings`` table and cached in memory.

Worker threads read the cached values on every inference tick; updates replace the whole
(immutable) model object, so readers never observe a partially updated value.
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yardlens_api.db.models import AppSetting
from yardlens_api.schemas.settings import (
    ClusterSettings,
    DetectionSettings,
    PerformanceSettings,
    TelegramSettings,
)

DETECTION_KEY = "detection"
TELEGRAM_KEY = "telegram"
PERFORMANCE_KEY = "performance"
CLUSTER_KEY = "cluster"


class SettingsStore:
    def __init__(self, *, default_model: str) -> None:
        self._detection = DetectionSettings(model=default_model)
        self._telegram = TelegramSettings()
        self._performance = PerformanceSettings()
        self._cluster = ClusterSettings()

    def detection(self) -> DetectionSettings:
        return self._detection

    def telegram(self) -> TelegramSettings:
        return self._telegram

    def performance(self) -> PerformanceSettings:
        return self._performance

    def cluster(self) -> ClusterSettings:
        return self._cluster

    async def load(self, session: AsyncSession) -> None:
        detection = await session.get(AppSetting, DETECTION_KEY)
        if detection is not None:
            self._detection = DetectionSettings.model_validate(detection.value)
        telegram = await session.get(AppSetting, TELEGRAM_KEY)
        if telegram is not None:
            self._telegram = TelegramSettings.model_validate(telegram.value)
        performance = await session.get(AppSetting, PERFORMANCE_KEY)
        if performance is not None:
            self._performance = PerformanceSettings.model_validate(performance.value)
        cluster = await session.get(AppSetting, CLUSTER_KEY)
        if cluster is not None:
            self._cluster = ClusterSettings.model_validate(cluster.value)

    async def save_detection(self, session: AsyncSession, value: DetectionSettings) -> None:
        await _upsert(session, DETECTION_KEY, value)
        self._detection = value

    async def save_telegram(self, session: AsyncSession, value: TelegramSettings) -> None:
        await _upsert(session, TELEGRAM_KEY, value)
        self._telegram = value

    async def save_performance(self, session: AsyncSession, value: PerformanceSettings) -> None:
        await _upsert(session, PERFORMANCE_KEY, value)
        self._performance = value

    async def save_cluster(self, session: AsyncSession, value: ClusterSettings) -> None:
        await _upsert(session, CLUSTER_KEY, value)
        self._cluster = value


async def _upsert(session: AsyncSession, key: str, value: BaseModel) -> None:
    payload: dict[str, Any] = value.model_dump(mode="json")
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=payload))
    else:
        row.value = payload
    await session.commit()
