"""The worker channel: what the API and a worker say to each other over one WebSocket.

A worker connects to ``/api/workers/connect`` with its token and says ``hello``. The server
answers ``welcome`` and ``assign``: the full set of cameras the worker is to run, which it brings
itself in line with (cameras missing from the set are stopped). From then on the worker reports
camera states, status changes, its load, events and loading sessions; the server acknowledges
events and sessions once they are saved, and the worker sends again whatever was not
acknowledged when a connection drops.

Text frames carry JSON. A message with files (photos, live frames) is a binary frame: a 4-byte
big-endian header length, the JSON header with the files' sizes under ``files``, then the files
back to back.
"""

import json
import struct
from collections.abc import Sequence
from typing import Annotated, Literal, cast

from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter, ValidationError

from yardlens_api.core.enums import CameraStatus
from yardlens_api.schemas.settings import DetectionSettings, PerformanceSettings
from yardlens_api.schemas.workers import NodeLoad, WorkerInfo
from yardlens_api.services.camera_worker import CameraConfig, RuntimeState, WorkerEvent
from yardlens_api.services.loading import SessionState

PROTOCOL_VERSION = 1
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_MESSAGE_BYTES = 3 * MAX_FILE_BYTES

# WebSocket close codes (4000-4999 are the application's own).
CLOSE_UNAUTHORIZED = 4401
"""The token is unknown or was revoked: the worker waits long before trying again."""
CLOSE_UNSUPPORTED = 4426
"""The worker speaks another protocol version: it needs the same release as the server."""
CLOSE_REPLACED = 4409
"""Another connection with the same token took over."""
CLOSE_PROTOCOL_ERROR = 4400


class ProtocolError(Exception):
    """A frame that is not a valid message."""


# -- worker -> server ----------------------------------------------------------------------------


class Hello(BaseModel):
    type: Literal["hello"] = "hello"
    protocol: int
    info: WorkerInfo
    running: dict[int, int] = Field(
        default_factory=dict[int, int],
        description="Cameras the worker already runs (after the server restarted), with epochs",
    )
    time: AwareDatetime = Field(description="The worker's clock")


class CameraReport(BaseModel):
    camera_id: int
    epoch: int
    state: RuntimeState


class States(BaseModel):
    """Every camera the worker runs, about once a second."""

    type: Literal["states"] = "states"
    cameras: list[CameraReport]


class Status(BaseModel):
    """A camera went online, offline or failed; sent at once."""

    type: Literal["status"] = "status"
    camera_id: int
    epoch: int
    status: CameraStatus
    last_error: str | None = None


class Resources(BaseModel):
    type: Literal["resources"] = "resources"
    load: NodeLoad


class EventReport(BaseModel):
    """An event, with its photo attached when it has one."""

    type: Literal["event"] = "event"
    seq: int
    epoch: int
    event: WorkerEvent


class SessionReport(BaseModel):
    """The whole loading session. ``images`` names the attached photos, in order: those that
    changed since the server last acknowledged them."""

    type: Literal["session"] = "session"
    seq: int
    epoch: int
    session: SessionState
    images: list[str] = Field(default_factory=list[str])


class LiveFrame(BaseModel):
    """The newest annotated frame of a camera someone watches; the JPEG is attached."""

    type: Literal["frame"] = "frame"
    camera_id: int
    seq: int


class RawFrame(BaseModel):
    """The answer to ``raw``: the frame without annotations attached, or nothing."""

    type: Literal["raw_frame"] = "raw_frame"
    request_id: int


class Pong(BaseModel):
    type: Literal["pong"] = "pong"
    id: int
    time: AwareDatetime = Field(description="The worker's clock when it answered")


type WorkerMessage = Annotated[
    Hello | States | Status | Resources | EventReport | SessionReport | LiveFrame | RawFrame | Pong,
    Field(discriminator="type"),
]


# -- server -> worker ----------------------------------------------------------------------------


class NodeSettings(BaseModel):
    detection: DetectionSettings
    performance: PerformanceSettings


class Welcome(BaseModel):
    type: Literal["welcome"] = "welcome"
    worker_id: int
    name: str
    settings: NodeSettings


class Assignment(BaseModel):
    config: CameraConfig
    epoch: int
    resume: SessionState | None = Field(
        default=None, description="An open loading session to carry on (in the worker's clock)"
    )
    resume_seen: AwareDatetime | None = None


class Assign(BaseModel):
    """The cameras the worker runs from now on. Running cameras that are missing are stopped:
    those in ``stop`` for good (their open sessions closed), the rest handed over (left open
    for the node that takes them)."""

    type: Literal["assign"] = "assign"
    cameras: list[Assignment]
    stop: list[int] = Field(default_factory=list[int])


class SettingsUpdate(BaseModel):
    type: Literal["settings"] = "settings"
    settings: NodeSettings


class Watch(BaseModel):
    """Cameras someone is watching: their live frames are wanted. Renewed every couple of
    seconds; a camera not renewed stops sending."""

    type: Literal["watch"] = "watch"
    cameras: list[int]


class RawRequest(BaseModel):
    type: Literal["raw"] = "raw"
    request_id: int
    camera_id: int


class Ack(BaseModel):
    """Events and sessions saved (by ``seq``): the worker forgets them."""

    type: Literal["ack"] = "ack"
    seqs: list[int]


class Ping(BaseModel):
    type: Literal["ping"] = "ping"
    id: int


type ServerMessage = Annotated[
    Welcome | Assign | SettingsUpdate | Watch | RawRequest | Ack | Ping,
    Field(discriminator="type"),
]

WORKER_MESSAGES: TypeAdapter[WorkerMessage] = TypeAdapter(WorkerMessage)
SERVER_MESSAGES: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)

_LENGTH = struct.Struct(">I")


def pack(message: BaseModel, files: Sequence[bytes] = ()) -> str | bytes:
    """A message as a WebSocket frame: text, or binary when files go along."""
    if not files:
        return message.model_dump_json()
    header = message.model_dump(mode="json") | {"files": [len(file) for file in files]}
    encoded = json.dumps(header, separators=(",", ":")).encode()
    return _LENGTH.pack(len(encoded)) + encoded + b"".join(files)


def unpack[T](adapter: TypeAdapter[T], frame: str | bytes) -> tuple[T, list[bytes]]:
    """The message in a frame and its files. Raises ``ProtocolError`` for anything malformed."""
    try:
        if isinstance(frame, str):
            return adapter.validate_json(frame), []
        if len(frame) > MAX_MESSAGE_BYTES or len(frame) < _LENGTH.size:
            raise ProtocolError("Frame size out of range")
        (length,) = _LENGTH.unpack_from(frame)
        end = _LENGTH.size + length
        decoded: object = json.loads(frame[_LENGTH.size : end])
        if not isinstance(decoded, dict):
            raise ProtocolError("Header is not an object")
        header = cast("dict[str, object]", decoded)
        listed = header.pop("files", [])
        sizes = (
            [size for size in cast("list[object]", listed) if isinstance(size, int)]
            if isinstance(listed, list)
            else []
        )
        if (
            not isinstance(listed, list)
            or len(sizes) != len(cast("list[object]", listed))
            or not all(0 <= size <= MAX_FILE_BYTES for size in sizes)
        ):
            raise ProtocolError("Bad file sizes")
        if end + sum(sizes) != len(frame):
            raise ProtocolError("File sizes do not add up")
        files: list[bytes] = []
        offset = end
        for size in sizes:
            files.append(frame[offset : offset + size])
            offset += size
        return adapter.validate_python(header), files
    except (ValidationError, ValueError, struct.error) as exc:
        raise ProtocolError(str(exc)) from exc
