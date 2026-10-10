"""In-process pub/sub used to fan out live messages (events, camera status) to SSE clients."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

type MessageKind = Literal["event", "camera_status"]


@dataclass(frozen=True, slots=True)
class BusMessage:
    kind: MessageKind
    data: str


class EventBus:
    def __init__(self, *, subscriber_queue_size: int = 256) -> None:
        self._subscribers: set[asyncio.Queue[BusMessage]] = set()
        self._queue_size = subscriber_queue_size

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, message: BusMessage) -> None:
        """Deliver to every subscriber; slow subscribers drop their oldest message."""
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[BusMessage]]:
        queue: asyncio.Queue[BusMessage] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
