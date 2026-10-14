import json
import logging
import sys
from datetime import UTC, datetime
from typing import override

# color_message is uvicorn's ANSI-colored duplicate of the message, passed as an ``extra``.
_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "color_message"}
)


class JsonFormatter(logging.Formatter):
    """Single-line JSON log records; extra fields passed via ``extra=`` are included."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _RESERVED_ATTRS and not key.startswith("_")
            }
        )
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class KeyValueFormatter(logging.Formatter):
    """Human-friendly format that still carries structured ``extra`` fields as key=value pairs."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    @override
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_ATTRS and not key.startswith("_")
        }
        if not extras:
            return base
        rendered = " ".join(f"{key}={value}" for key, value in extras.items())
        return f"{base} [{rendered}]"


def configure_logging(level: str, *, json_output: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else KeyValueFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for noisy in ("httpx", "httpcore", "ultralytics", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
