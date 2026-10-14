"""Telegram notifications for events."""

import asyncio
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from yardlens_api.core.enums import EventType
from yardlens_api.core.i18n import CAMERA_LABEL, EVENT_TITLES, LOADING_LABELS, Language, class_label
from yardlens_api.schemas.settings import TelegramSettings
from yardlens_api.services.plates import format_plate

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


# Loading events are one per vehicle and always worth a message, so they skip rate limiting.
_LOADING_EVENTS = frozenset({EventType.VEHICLE_ARRIVED, EventType.LOADING_COMPLETED})


def event_caption(
    language: Language,
    event_type: EventType,
    class_name: str | None,
    camera_name: str,
    details: Mapping[str, Any] | None = None,
) -> str:
    title = EVENT_TITLES[language][event_type]
    subject = f" · {class_label(class_name, language)}" if class_name else ""
    lines = [f"{title}{subject}"]
    if details and event_type in _LOADING_EVENTS:
        lines += _loading_lines(language, details)
    lines.append(f"{CAMERA_LABEL[language]}: {camera_name}")
    return "\n".join(lines)


def _loading_lines(language: Language, details: Mapping[str, Any]) -> list[str]:
    labels = LOADING_LABELS[language]
    lines: list[str] = []
    plate = details.get("plate")
    if isinstance(plate, str) and plate:
        lines.append(f"{labels['plate']}: {format_plate(plate)}")
    loaded = details.get("loaded")
    if isinstance(loaded, int):
        lines.append(f"{labels['loaded']}: {loaded}")
    unloaded = details.get("unloaded")
    if isinstance(unloaded, int) and unloaded > 0:
        lines.append(f"{labels['unloaded']}: {unloaded}")
    duration = details.get("duration_seconds")
    if isinstance(duration, int):
        lines.append(f"{labels['duration']}: {max(1, round(duration / 60))} {labels['minutes']}")
    return lines


class TelegramError(Exception):
    pass


class TelegramNotifier:
    def __init__(self, client: httpx.AsyncClient, *, api_base: str = TELEGRAM_API) -> None:
        self._client = client
        self._api_base = api_base
        self._last_sent: dict[tuple[int, EventType], float] = {}

    async def send_message(self, token: str, chat_id: str, text: str) -> None:
        await self._call(token, "sendMessage", data={"chat_id": chat_id, "text": text})

    async def send_photo(self, token: str, chat_id: str, caption: str, photo: bytes) -> None:
        await self._call(
            token,
            "sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("snapshot.jpg", photo, "image/jpeg")},
        )

    async def notify_event(
        self,
        settings: TelegramSettings,
        *,
        camera_id: int,
        camera_name: str,
        event_type: EventType,
        class_name: str | None,
        snapshot: Path | None,
        details: Mapping[str, Any] | None = None,
    ) -> bool:
        """Send a notification if enabled and not rate limited. Never raises."""
        if not (settings.enabled and settings.bot_token and settings.chat_id):
            return False
        if event_type not in settings.notify_events:
            return False
        key = (camera_id, event_type)
        now = time.monotonic()
        last = self._last_sent.get(key)
        if (
            event_type not in _LOADING_EVENTS
            and last is not None
            and now - last < settings.min_interval_seconds
        ):
            return False
        self._last_sent[key] = now

        caption = event_caption(settings.language, event_type, class_name, camera_name, details)
        photo = await _read_snapshot(snapshot) if snapshot is not None else None
        try:
            if photo is not None:
                await self.send_photo(settings.bot_token, settings.chat_id, caption, photo)
            else:
                await self.send_message(settings.bot_token, settings.chat_id, caption)
        except TelegramError as exc:
            logger.warning("Telegram notification failed", extra={"error": str(exc)})
            return False
        return True

    async def _call(
        self,
        token: str,
        method: str,
        *,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> None:
        url = f"{self._api_base}/bot{token}/{method}"
        try:
            response = await self._client.post(url, data=data, files=files, timeout=15.0)
        except httpx.HTTPError as exc:
            raise TelegramError(f"Could not reach Telegram: {type(exc).__name__}") from exc
        if not response.is_success:
            raise TelegramError(_describe_error(response))


async def _read_snapshot(path: Path) -> bytes | None:
    try:
        return await asyncio.to_thread(path.read_bytes)
    except OSError:
        return None


def _describe_error(response: httpx.Response) -> str:
    try:
        payload: object = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        description = payload.get("description")  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        if isinstance(description, str):
            return f"Telegram API error: {description}"
    return f"Telegram API error: HTTP {response.status_code}"
