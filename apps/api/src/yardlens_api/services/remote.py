"""A connected worker as the server sees it: the connection, what it reports, its live frames.

One task reads the socket and hands each message to the cluster; one writes, from a queue, so
nothing ever waits on a slow socket while holding anything else up. A third keeps the watch list
and the clock estimate fresh.
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from starlette.websockets import WebSocket, WebSocketDisconnect

from yardlens_api.schemas.worker_protocol import (
    CLOSE_PROTOCOL_ERROR,
    WORKER_MESSAGES,
    Ack,
    Hello,
    Ping,
    Pong,
    ProtocolError,
    RawRequest,
    Watch,
    WorkerMessage,
    pack,
    unpack,
)
from yardlens_api.schemas.workers import NodeLoad, WorkerInfo
from yardlens_api.services.camera_worker import CameraConfig, RuntimeState

logger = logging.getLogger(__name__)

type Handler = Callable[[WorkerMessage, list[bytes]], Awaitable[None]]

# Live frames are wanted while someone has looked within this long; the worker is reminded at
# least this often, and stops sending once it has not been for a while.
VIEW_SECONDS = 5.0
WATCH_RENEW_SECONDS = 2.0
TICK_SECONDS = 0.5
PING_SECONDS = 10.0
RAW_TIMEOUT_SECONDS = 5.0
# Clocks closer than this are taken as equal, so times do not jitter by a few milliseconds.
CLOCK_TOLERANCE = timedelta(seconds=1)
_CLOCK_SAMPLES = 8


class RemoteNode:
    def __init__(
        self,
        *,
        worker_id: int,
        name: str,
        websocket: WebSocket,
        hello: Hello,
        address: str | None,
    ) -> None:
        self.worker_id = worker_id
        self.name = name
        self.info: WorkerInfo = hello.info
        self.address = address
        self.connected_at = datetime.now(UTC)
        self.load: NodeLoad | None = None
        self.states: dict[int, RuntimeState] = {}
        """Latest state of each camera this worker runs for the cluster (current epoch only)."""
        self.running: dict[int, int] = dict(hello.running)
        """Camera -> epoch the worker runs it under, as far as the server knows."""
        self.stop_for_good: set[int] = set()
        """Cameras to name in the next ``assign`` as stopped for good (disabled or deleted)."""
        self.configs: dict[int, CameraConfig] = {}
        """The configuration last sent for each camera."""
        self._websocket = websocket
        self._outbox: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self._close: tuple[int, str] = (1000, "")
        self._acks: list[int] = []
        self._frames: dict[int, tuple[int, bytes, float]] = {}
        self._viewed: dict[int, float] = {}
        self._watching: list[int] = []
        self._watch_sent = float("-inf")
        self._raw: dict[int, asyncio.Future[bytes | None]] = {}
        self._next_id = 0
        self._pings: dict[int, tuple[float, datetime]] = {}
        # First guess from the hello (off by the one-way delay), refined by pings.
        self._clock: deque[tuple[float, timedelta]] = deque(maxlen=_CLOCK_SAMPLES)
        self._clock.append((float("inf"), datetime.now(UTC) - hello.time))

    # -- sending ---------------------------------------------------------------------------------

    def send(self, message: BaseModel, files: tuple[bytes, ...] = ()) -> None:
        self._outbox.put_nowait(pack(message, files))

    def ack(self, seq: int) -> None:
        """Acknowledge a saved event or session; acks made in one go share a message."""
        if not self._acks:
            asyncio.get_running_loop().call_soon(self._flush_acks)
        self._acks.append(seq)

    def _flush_acks(self) -> None:
        if self._acks:
            self.send(Ack(seqs=self._acks))
            self._acks = []

    def close(self, code: int, reason: str = "") -> None:
        """Close the connection once what is queued before it has gone out."""
        self._close = (code, reason)
        self._outbox.put_nowait(None)

    async def _write(self) -> None:
        while True:
            frame = await self._outbox.get()
            if frame is None:
                code, reason = self._close
                with contextlib.suppress(Exception):
                    await self._websocket.close(code=code, reason=reason)
                return
            if isinstance(frame, str):
                await self._websocket.send_text(frame)
            else:
                await self._websocket.send_bytes(frame)

    # -- clock -----------------------------------------------------------------------------------

    @property
    def clock_offset(self) -> timedelta:
        """Add to the worker's times to get the server's: the estimate from the fastest of the
        recent round trips."""
        _, offset = min(self._clock, key=lambda sample: sample[0])
        return offset

    @property
    def correction(self) -> timedelta:
        offset = self.clock_offset
        return offset if abs(offset) >= CLOCK_TOLERANCE else timedelta(0)

    def _ping(self) -> None:
        self._next_id += 1
        self._pings[self._next_id] = (time.monotonic(), datetime.now(UTC))
        self.send(Ping(id=self._next_id))

    def pong(self, message: Pong) -> None:
        sent = self._pings.pop(message.id, None)
        if sent is None:
            return
        round_trip = time.monotonic() - sent[0]
        midpoint = sent[1] + timedelta(seconds=round_trip / 2)
        self._clock.append((round_trip, midpoint - message.time))

    # -- live view -------------------------------------------------------------------------------

    def viewed(self, camera_id: int) -> None:
        """Someone looks at the camera: make sure its frames come."""
        self._viewed[camera_id] = time.monotonic()
        if camera_id not in self._watching:
            self._send_watch()

    def frame(self, camera_id: int) -> tuple[int, bytes | None]:
        entry = self._frames.get(camera_id)
        if entry is None or time.monotonic() - entry[2] > VIEW_SECONDS:
            return (entry[0] if entry else 0), None
        return entry[0], entry[1]

    def put_frame(self, camera_id: int, seq: int, jpeg: bytes) -> None:
        self._frames[camera_id] = (seq, jpeg, time.monotonic())

    async def raw(self, camera_id: int) -> bytes | None:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[bytes | None] = asyncio.get_running_loop().create_future()
        self._raw[request_id] = future
        self.send(RawRequest(request_id=request_id, camera_id=camera_id))
        try:
            return await asyncio.wait_for(future, RAW_TIMEOUT_SECONDS)
        except TimeoutError:
            return None
        finally:
            self._raw.pop(request_id, None)

    def put_raw(self, request_id: int, jpeg: bytes | None) -> None:
        future = self._raw.get(request_id)
        if future is not None and not future.done():
            future.set_result(jpeg)

    def _send_watch(self) -> None:
        now = time.monotonic()
        self._viewed = {
            camera: at for camera, at in self._viewed.items() if now - at <= VIEW_SECONDS
        }
        self._watching = sorted(self._viewed)
        self._watch_sent = now
        self.send(Watch(cameras=self._watching))

    async def _tick(self) -> None:
        last_ping = float("-inf")
        while True:
            now = time.monotonic()
            if now - last_ping >= PING_SECONDS:
                last_ping = now
                self._ping()
            expired = any(now - self._viewed[camera] > VIEW_SECONDS for camera in self._watching)
            if expired or (self._watching and now - self._watch_sent >= WATCH_RENEW_SECONDS):
                self._send_watch()
            await asyncio.sleep(TICK_SECONDS)

    # -- serving ---------------------------------------------------------------------------------

    async def run(self, handle: Handler) -> None:
        """Serve the connection until it closes."""
        writer = asyncio.create_task(self._write(), name=f"worker-{self.worker_id}-writer")
        ticker = asyncio.create_task(self._tick(), name=f"worker-{self.worker_id}-ticker")
        try:
            while True:
                received = await self._websocket.receive()
                if received["type"] == "websocket.disconnect":
                    return
                frame: str | bytes | None = received.get("text")
                if frame is None:
                    frame = received.get("bytes")
                if frame is None:
                    continue
                try:
                    message, files = unpack(WORKER_MESSAGES, frame)
                except ProtocolError as exc:
                    logger.warning(
                        "Malformed message from a worker; closing",
                        extra={"worker_id": self.worker_id, "error": str(exc)},
                    )
                    self.close(CLOSE_PROTOCOL_ERROR, "Malformed message")
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(writer, 5.0)
                    return
                await handle(message, files)
                if writer.done():
                    return  # closed from this side (replaced, removed, shutting down)
        except WebSocketDisconnect, RuntimeError:
            return
        finally:
            ticker.cancel()
            if not writer.done():
                writer.cancel()
            for future in self._raw.values():
                if not future.done():
                    future.set_result(None)
