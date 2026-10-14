"""Query parameter checks shared by the routes."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import status

from yardlens_api.core.errors import AppError


class InvalidParameterError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


def parse_timezone(tz: str) -> ZoneInfo:
    """An IANA zone name such as ``Asia/Tashkent``; used to define "today" and local days."""
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidParameterError(f"Unknown timezone '{tz}'") from exc


def check_range(since: datetime | None, until: datetime | None) -> None:
    if since is not None and until is not None and since >= until:
        raise InvalidParameterError("'since' must be earlier than 'until'")
