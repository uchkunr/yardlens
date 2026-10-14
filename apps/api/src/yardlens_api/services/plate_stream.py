"""The camera's main (high-resolution) stream, opened only while a plate is being read.

IP cameras usually offer two streams: a full-resolution main stream and a light sub-stream.
Detection and counting run on the sub-stream; decoding a 4K main stream around the clock would
cost more CPU than everything else together, and the plate is the only thing that needs it.

While a vehicle stands in the bay and its plate is still uncertain, the worker calls ``want`` on
every tick. The stream connects in the background (a second or two), keeps its newest frame
converted a couple of times a second, and disconnects ``linger`` seconds after the last ``want``.
"""

import logging
import threading
import time
from collections.abc import Callable

from yardlens_api.core.errors import SourceError
from yardlens_api.services.sources import (
    Frame,
    FrameReader,
    ReaderKind,
    ResolvedSource,
    VideoCaptureReader,
)

logger = logging.getLogger(__name__)

type ReaderFactory = Callable[[], FrameReader]

RETRY_SECONDS = 5.0


class PlateStream:
    def __init__(
        self,
        url: str,
        *,
        timeout: float = 15.0,
        hardware_decode: bool = False,
        decode_threads: int = 0,
        interval: float = 0.5,
        linger: float = 15.0,
        opener: ReaderFactory | None = None,
    ) -> None:
        self.url = url
        self._open: ReaderFactory = opener or (
            lambda: VideoCaptureReader(
                ResolvedSource(uri=url, kind=ReaderKind.VIDEO),
                open_timeout=timeout,
                hardware_decode=hardware_decode,
                threads=decode_threads,
            )
        )
        self._interval = interval
        self._linger = linger
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wanted_until = 0.0
        self._thread: threading.Thread | None = None
        self._frame: Frame | None = None
        self._frame_at = 0.0

    @property
    def connected(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def want(self) -> None:
        """Keep the stream open (or open it) for another ``linger`` seconds."""
        with self._lock:
            self._wanted_until = time.monotonic() + self._linger
            if self._stop.is_set() or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = threading.Thread(target=self._run, name="plate-stream", daemon=True)
            self._thread.start()

    def latest(self, max_age: float = 2.0) -> Frame | None:
        """The newest high-resolution frame, unless it is older than ``max_age`` seconds."""
        with self._lock:
            if self._frame is None or time.monotonic() - self._frame_at > max_age:
                return None
            return self._frame

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

    def _wanted(self) -> bool:
        return not self._stop.is_set() and time.monotonic() < self._wanted_until

    def _run(self) -> None:
        while self._wanted():
            try:
                reader = self._open()
            except SourceError as exc:
                logger.warning("Main stream unavailable", extra={"error": str(exc)})
                self._stop.wait(RETRY_SECONDS)
                continue
            except Exception:
                logger.exception("Could not open the main stream")
                self._stop.wait(RETRY_SECONDS)
                continue
            logger.info("Main stream opened for plate reading")
            try:
                self._pump(reader)
            finally:
                reader.close()
        with self._lock:
            self._frame = None
        logger.info("Main stream closed")

    def _pump(self, reader: FrameReader) -> None:
        last = float("-inf")
        while self._wanted():
            if not reader.grab():
                return
            now = time.monotonic()
            if now - last < self._interval:
                continue
            frame = reader.retrieve()
            if frame is None:
                continue
            last = now
            with self._lock:
                self._frame = frame
                self._frame_at = now
