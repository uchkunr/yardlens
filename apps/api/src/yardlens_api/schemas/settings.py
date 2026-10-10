from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from yardlens_api.core.enums import DEFAULT_CLASSES, EventType

DEFAULT_NOTIFY_EVENTS: tuple[EventType, ...] = (
    EventType.LINE_CROSSED_IN,
    EventType.LINE_CROSSED_OUT,
    EventType.CAMERA_OFFLINE,
)


class DetectionSettings(BaseModel):
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] = (
        "yolo26n.pt"
    )
    confidence: Annotated[float, Field(ge=0.05, le=0.95)] = 0.35
    classes: Annotated[list[str], Field(min_length=1)] = Field(
        default_factory=lambda: list(DEFAULT_CLASSES)
    )


class TelegramSettings(BaseModel):
    """Stored form, including the secret bot token. Never returned by the API."""

    enabled: bool = False
    bot_token: str | None = None
    chat_id: str | None = None
    notify_events: list[EventType] = Field(default_factory=lambda: list(DEFAULT_NOTIFY_EVENTS))
    min_interval_seconds: Annotated[int, Field(ge=0, le=3600)] = 30


class TelegramSettingsRead(BaseModel):
    enabled: bool
    bot_token_set: bool
    bot_token_hint: str | None = Field(description="Masked token suffix, e.g. '••••a1b2'")
    chat_id: str | None
    notify_events: list[EventType]
    min_interval_seconds: int


class TelegramSettingsUpdate(BaseModel):
    enabled: bool | None = None
    bot_token: Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)] | None = (
        Field(default=None, description="New token; omit to keep the current one, '' to clear it")
    )
    chat_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    notify_events: list[EventType] | None = None
    min_interval_seconds: Annotated[int, Field(ge=0, le=3600)] | None = None


class SettingsRead(BaseModel):
    detection: DetectionSettings
    telegram: TelegramSettingsRead
    available_models: list[str]
    available_classes: list[str]


class SettingsUpdate(BaseModel):
    detection: DetectionSettings | None = None
    telegram: TelegramSettingsUpdate | None = None


class TelegramTestRequest(BaseModel):
    """Optional overrides so the form can be tested before it is saved."""

    bot_token: str | None = None
    chat_id: str | None = None


class TelegramTestResult(BaseModel):
    success: bool
    message: str
