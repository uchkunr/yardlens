from collections.abc import Sequence
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from yardlens_api.core.enums import DEFAULT_CLASSES, EventType
from yardlens_api.core.i18n import DEFAULT_LANGUAGE, Language

DEFAULT_NOTIFY_EVENTS: tuple[EventType, ...] = (
    EventType.LINE_CROSSED_IN,
    EventType.LINE_CROSSED_OUT,
    EventType.CAMERA_OFFLINE,
    EventType.LOADING_COMPLETED,
)


class DetectionSettings(BaseModel):
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] = (
        "yolo26n.pt"
    )
    confidence: Annotated[float, Field(ge=0.05, le=0.95)] = 0.35
    classes: Annotated[list[str], Field(min_length=1)] = Field(
        default_factory=lambda: list(DEFAULT_CLASSES)
    )
    cargo_classes: list[str] = Field(
        default_factory=list[str],
        description="Classes counted as cargo on loading-bay cameras; a subset of `classes`",
    )


ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class CameraDetection(BaseModel):
    """A camera's own detection settings. Fields left out (null) follow the general settings."""

    model: ModelName | None = Field(
        default=None, description="A model of its own, e.g. one trained for this loading bay"
    )
    confidence: Annotated[float, Field(ge=0.05, le=0.95)] | None = None
    classes: Annotated[list[str], Field(min_length=1)] | None = None
    cargo_classes: list[str] | None = None


def effective_detection(
    general: DetectionSettings, own: CameraDetection | None
) -> DetectionSettings:
    """What a camera actually uses: its own settings where it has them, the general ones
    elsewhere. Inherited cargo classes are limited to the classes the camera detects."""
    if own is None:
        return general
    classes = own.classes if own.classes is not None else general.classes
    cargo = (
        own.cargo_classes
        if own.cargo_classes is not None
        else [name for name in general.cargo_classes if name in classes]
    )
    # Both inputs are validated already; constructing skips re-validating on every frame.
    return DetectionSettings.model_construct(
        model=own.model or general.model,
        confidence=own.confidence if own.confidence is not None else general.confidence,
        classes=classes,
        cargo_classes=cargo,
    )


def detection_problem(detection: DetectionSettings, class_names: Sequence[str]) -> str | None:
    """Why these settings cannot work with a model detecting ``class_names``, if they cannot."""
    unknown = sorted(set(detection.classes) - set(class_names))
    if unknown:
        return f"Unknown classes: {', '.join(unknown)}"
    if not set(detection.cargo_classes) <= set(detection.classes):
        return "Cargo classes must be among the detected classes"
    return None


class PerformanceSettings(BaseModel):
    cpu_limit_percent: Annotated[int, Field(ge=30, le=95)] = Field(
        default=75,
        description="CPU load the governor keeps the machine under, by lowering frame rates",
    )
    adaptive_fps: bool = Field(
        default=True, description="Slow down inference on cameras where nothing moves"
    )
    idle_fps: Annotated[float, Field(ge=0.2, le=5.0)] = Field(
        default=1.0, description="Inference rate of a camera while nothing moves"
    )


class TelegramSettings(BaseModel):
    """Stored form, including the secret bot token. Never returned by the API."""

    enabled: bool = False
    bot_token: str | None = None
    chat_id: str | None = None
    notify_events: list[EventType] = Field(default_factory=lambda: list(DEFAULT_NOTIFY_EVENTS))
    min_interval_seconds: Annotated[int, Field(ge=0, le=3600)] = 30
    language: Language = DEFAULT_LANGUAGE


class TelegramSettingsRead(BaseModel):
    enabled: bool
    bot_token_set: bool
    bot_token_hint: str | None = Field(description="Masked token suffix, e.g. '••••a1b2'")
    chat_id: str | None
    notify_events: list[EventType]
    min_interval_seconds: int
    language: Language = Field(description="Language of the notification text")


class TelegramSettingsUpdate(BaseModel):
    enabled: bool | None = None
    bot_token: Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)] | None = (
        Field(default=None, description="New token; omit to keep the current one, '' to clear it")
    )
    chat_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    notify_events: list[EventType] | None = None
    min_interval_seconds: Annotated[int, Field(ge=0, le=3600)] | None = None
    language: Language | None = None


class SettingsRead(BaseModel):
    detection: DetectionSettings
    telegram: TelegramSettingsRead
    performance: PerformanceSettings
    available_models: list[str]
    available_classes: list[str]


class SettingsUpdate(BaseModel):
    detection: DetectionSettings | None = None
    telegram: TelegramSettingsUpdate | None = None
    performance: PerformanceSettings | None = None


class TelegramTestRequest(BaseModel):
    """Optional overrides so the form can be tested before it is saved."""

    bot_token: str | None = None
    chat_id: str | None = None
    language: Language | None = None


class TelegramTestResult(BaseModel):
    success: bool
    message: str
