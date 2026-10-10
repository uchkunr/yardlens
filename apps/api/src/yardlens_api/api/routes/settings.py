import asyncio
import logging

from fastapi import APIRouter, status

from yardlens_api.api.deps import Container, ContainerDep, SessionDep
from yardlens_api.core.errors import AppError
from yardlens_api.schemas.settings import (
    SettingsRead,
    SettingsUpdate,
    TelegramSettings,
    TelegramSettingsRead,
    TelegramTestRequest,
    TelegramTestResult,
)
from yardlens_api.services.detector import AVAILABLE_MODELS
from yardlens_api.services.notifier import TelegramError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


class InvalidSettingsError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


def _mask(token: str | None) -> str | None:
    if not token:
        return None
    return f"••••{token[-4:]}"


def _read(container: Container) -> SettingsRead:
    telegram = container.settings_store.telegram()
    return SettingsRead(
        detection=container.settings_store.detection(),
        telegram=TelegramSettingsRead(
            enabled=telegram.enabled,
            bot_token_set=bool(telegram.bot_token),
            bot_token_hint=_mask(telegram.bot_token),
            chat_id=telegram.chat_id,
            notify_events=telegram.notify_events,
            min_interval_seconds=telegram.min_interval_seconds,
        ),
        available_models=list(AVAILABLE_MODELS),
        available_classes=list(container.detector.class_names),
    )


@router.get("", response_model=SettingsRead)
async def get_settings(container: ContainerDep) -> SettingsRead:
    return _read(container)


@router.put("", response_model=SettingsRead)
async def update_settings(
    payload: SettingsUpdate, container: ContainerDep, session: SessionDep
) -> SettingsRead:
    store = container.settings_store
    if payload.detection is not None:
        detection = payload.detection
        unknown = sorted(set(detection.classes) - set(container.detector.class_names))
        if unknown:
            raise InvalidSettingsError(f"Unknown classes: {', '.join(unknown)}")
        if detection.model not in AVAILABLE_MODELS:
            raise InvalidSettingsError(f"Unsupported model '{detection.model}'")
        if detection.model != container.detector.status.model:
            try:
                await asyncio.to_thread(container.detector.load, detection.model)
            except Exception as exc:
                logger.exception("Model load failed", extra={"model": detection.model})
                raise InvalidSettingsError(f"Could not load model: {exc}") from exc
        await store.save_detection(session, detection)

    if payload.telegram is not None:
        current = store.telegram()
        changes = payload.telegram.model_dump(exclude_unset=True)
        if "bot_token" in changes:
            changes["bot_token"] = changes["bot_token"] or None
        merged = TelegramSettings.model_validate(current.model_dump() | changes)
        if merged.enabled and not (merged.bot_token and merged.chat_id):
            raise InvalidSettingsError("Bot token and chat ID are required to enable Telegram")
        await store.save_telegram(session, merged)

    return _read(container)


@router.post("/telegram/test", response_model=TelegramTestResult)
async def test_telegram(
    payload: TelegramTestRequest, container: ContainerDep
) -> TelegramTestResult:
    stored = container.settings_store.telegram()
    token = payload.bot_token or stored.bot_token
    chat_id = payload.chat_id or stored.chat_id
    if not token or not chat_id:
        return TelegramTestResult(success=False, message="Bot token and chat ID are required")
    try:
        await container.notifier.send_message(
            token, chat_id, "✅ YardLens test message: notifications are working."
        )
    except TelegramError as exc:
        return TelegramTestResult(success=False, message=str(exc))
    return TelegramTestResult(success=True, message="Test message sent")
