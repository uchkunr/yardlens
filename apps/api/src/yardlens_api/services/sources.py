"""Camera source resolution and frame readers.

A camera is configured with a ``SourceType`` and a URL. ``resolve_source`` validates the pair and
turns it into a ``ResolvedSource`` (for YouTube this means asking yt-dlp for the live HLS URL).
``open_reader`` then opens a ``FrameReader`` that yields decoded BGR frames.
"""

import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse
from urllib.request import url2pathname

import cv2
import httpx
import numpy as np
from numpy.typing import NDArray

from yardlens_api.core.enums import SourceType
from yardlens_api.core.errors import SourceError

logger = logging.getLogger(__name__)

type Frame = NDArray[np.uint8]

_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
)
_JPEG_START = b"\xff\xd8"
_JPEG_END = b"\xff\xd9"
_MAX_MJPEG_BUFFER = 8 * 1024 * 1024
_HTTP_HEADERS = {"User-Agent": "YardLens/0.1"}


class ReaderKind(StrEnum):
    VIDEO = "video"
    MJPEG = "mjpeg"
    SNAPSHOT = "snapshot"


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    uri: str
    kind: ReaderKind
    loop: bool = False


class FrameReader(Protocol):
    """Blocking frame reader. ``read`` returns ``None`` when the stream has ended or stalled."""

    @property
    def native_fps(self) -> float | None: ...

    def read(self) -> Frame | None: ...

    def close(self) -> None: ...


type YoutubeResolver = Callable[[str], str]


def _require_scheme(url: str, schemes: set[str], label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in schemes or not parsed.netloc:
        expected = " or ".join(sorted(f"{scheme}://" for scheme in schemes))
        raise SourceError(f"{label} URL must start with {expected}")


def validate_source(source_type: SourceType, url: str, *, base_dir: Path | None = None) -> None:
    """Cheap, offline validation used by the API before persisting a camera."""
    match source_type:
        case SourceType.RTSP:
            _require_scheme(url, {"rtsp", "rtsps"}, "RTSP")
        case SourceType.HTTP | SourceType.HLS:
            _require_scheme(url, {"http", "https"}, source_type.value.upper())
        case SourceType.YOUTUBE:
            _require_scheme(url, {"http", "https"}, "YouTube")
            host = (urlparse(url).hostname or "").lower()
            if host not in _YOUTUBE_HOSTS:
                raise SourceError("YouTube URL must point to youtube.com or youtu.be")
        case SourceType.FILE:
            path = resolve_file_path(url, base_dir=base_dir)
            if not path.is_file():
                raise SourceError(f"Video file not found: {path}")


def resolve_file_path(url: str, *, base_dir: Path | None = None) -> Path:
    raw = url.removeprefix("file://")
    if raw != url and raw.startswith("/"):
        # file:///C:/clips/a.mp4 becomes C:\clips\a.mp4 on Windows; %20 and friends are decoded.
        raw = url2pathname(raw)
    path = Path(raw).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def resolve_youtube(url: str) -> str:
    """Resolve a YouTube watch URL to a direct HLS/media URL using yt-dlp."""
    from yt_dlp import YoutubeDL  # pyright: ignore[reportMissingTypeStubs]

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "best[height<=720][protocol^=m3u8]/best[height<=720]/best",
        "js_runtimes": {"deno": {}, "node": {}, "bun": {}},
    }
    try:
        with YoutubeDL(cast("Any", options)) as ydl:
            info = cast("dict[str, Any] | None", ydl.extract_info(url, download=False))  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        raise SourceError(f"yt-dlp could not resolve the stream: {_short_error(exc)}") from exc
    if not info:
        raise SourceError("yt-dlp returned no stream information")
    stream_url = info.get("url")
    if not isinstance(stream_url, str) or not stream_url:
        raise SourceError("No playable stream found for this YouTube URL")
    return stream_url


def _short_error(exc: BaseException) -> str:
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
    message = message.removeprefix("ERROR: ")
    return message[:300] if message else type(exc).__name__


def probe_http_kind(url: str, *, timeout: float = 10.0) -> ReaderKind:
    """Distinguish an MJPEG stream from a single-image (snapshot) endpoint by content type."""
    try:
        with (
            httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
    except httpx.HTTPError as exc:
        raise SourceError(f"HTTP request failed: {_short_error(exc)}") from exc
    if "multipart" in content_type:
        return ReaderKind.MJPEG
    if content_type.startswith("image/"):
        return ReaderKind.SNAPSHOT
    raise SourceError(f"Unsupported HTTP content type '{content_type or 'unknown'}'")


def resolve_source(
    source_type: SourceType,
    url: str,
    *,
    base_dir: Path | None = None,
    youtube_resolver: YoutubeResolver = resolve_youtube,
    http_prober: Callable[[str], ReaderKind] = probe_http_kind,
) -> ResolvedSource:
    validate_source(source_type, url, base_dir=base_dir)
    match source_type:
        case SourceType.RTSP | SourceType.HLS:
            return ResolvedSource(uri=url, kind=ReaderKind.VIDEO)
        case SourceType.YOUTUBE:
            return ResolvedSource(uri=youtube_resolver(url), kind=ReaderKind.VIDEO)
        case SourceType.HTTP:
            return ResolvedSource(uri=url, kind=http_prober(url))
        case SourceType.FILE:
            path = resolve_file_path(url, base_dir=base_dir).resolve()
            return ResolvedSource(uri=str(path), kind=ReaderKind.VIDEO, loop=True)


class VideoCaptureReader:
    """OpenCV/FFmpeg reader for RTSP, HLS and local files (optionally looped)."""

    def __init__(self, source: ResolvedSource, *, open_timeout: float = 15.0) -> None:
        self._source = source
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            int(open_timeout * 1000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            int(open_timeout * 1000),
        ]
        self._capture = cv2.VideoCapture(source.uri, cv2.CAP_FFMPEG, params)
        if not self._capture.isOpened():
            self._capture.release()
            raise SourceError("Could not open the video stream")
        fps = float(self._capture.get(cv2.CAP_PROP_FPS))
        self._native_fps = fps if 1.0 <= fps <= 120.0 else None

    @property
    def native_fps(self) -> float | None:
        return self._native_fps

    @property
    def position_seconds(self) -> float | None:
        """Presentation timestamp of the last frame read, if the container provides one."""
        position = float(self._capture.get(cv2.CAP_PROP_POS_MSEC))
        return position / 1000.0 if position > 0 else None

    def read(self) -> Frame | None:
        ok, frame = self._capture.read()
        if ok:
            return cast("Frame", frame)
        if self._source.loop:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._capture.read()
            if ok:
                return cast("Frame", frame)
        return None

    def close(self) -> None:
        self._capture.release()


def decode_jpeg(data: bytes) -> Frame | None:
    buffer = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    return cast("Frame | None", frame)


def iter_jpeg_frames(chunks: Iterator[bytes]) -> Iterator[bytes]:
    """Extract complete JPEG images from an MJPEG byte stream, independent of boundaries."""
    buffer = bytearray()
    for chunk in chunks:
        buffer.extend(chunk)
        while True:
            start = buffer.find(_JPEG_START)
            if start < 0:
                # Keep a trailing 0xFF: it may be the first byte of a start marker.
                del buffer[: len(buffer) - 1 if buffer.endswith(b"\xff") else len(buffer)]
                break
            end = buffer.find(_JPEG_END, start + 2)
            if end < 0:
                if start > 0:
                    del buffer[:start]
                break
            yield bytes(buffer[start : end + 2])
            del buffer[: end + 2]
        if len(buffer) > _MAX_MJPEG_BUFFER:
            buffer.clear()


class MjpegReader:
    """Reads ``multipart/x-mixed-replace`` JPEG streams over HTTP."""

    def __init__(self, url: str, *, timeout: float = 15.0) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, read=timeout),
            follow_redirects=True,
            headers=_HTTP_HEADERS,
        )
        self._closed = threading.Event()
        try:
            request = self._client.build_request("GET", url)
            self._response = self._client.send(request, stream=True)
            self._response.raise_for_status()
        except httpx.HTTPError as exc:
            self._client.close()
            raise SourceError(f"MJPEG request failed: {_short_error(exc)}") from exc
        self._frames = iter_jpeg_frames(self._response.iter_bytes())

    @property
    def native_fps(self) -> float | None:
        return None

    def read(self) -> Frame | None:
        if self._closed.is_set():
            return None
        try:
            for jpeg in self._frames:
                frame = decode_jpeg(jpeg)
                if frame is not None:
                    return frame
        except httpx.HTTPError, httpx.StreamError:
            return None
        return None

    def close(self) -> None:
        self._closed.set()
        self._response.close()
        self._client.close()


class SnapshotReader:
    """Polls a JPEG snapshot URL. The worker controls the polling rate via ``min_interval``."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 15.0,
        min_interval: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._url = url
        self._min_interval = min_interval
        self._last_fetch = 0.0
        self._client = httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS)
        self._stop = stop_event or threading.Event()

    @property
    def native_fps(self) -> float | None:
        return 1.0 / self._min_interval

    def read(self) -> Frame | None:
        wait = self._min_interval - (time.monotonic() - self._last_fetch)
        if wait > 0 and self._stop.wait(wait):
            return None
        self._last_fetch = time.monotonic()
        try:
            response = self._client.get(self._url)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        return decode_jpeg(response.content)

    def close(self) -> None:
        self._client.close()


class TimedFrameReader(FrameReader, Protocol):
    @property
    def position_seconds(self) -> float | None: ...


class PacedReader:
    """Releases video frames at the pace of their presentation timestamps (PTS).

    Files would otherwise decode as fast as possible, and HLS delivers whole segments in bursts;
    pacing by PTS turns both into smooth real-time playback. Missing timestamps fall back to the
    native frame rate. Jumps (a looped file restarting, a stream discontinuity) or falling more
    than ``max_lag`` seconds behind re-anchor the clock instead of accumulating delay.
    """

    def __init__(
        self,
        inner: TimedFrameReader,
        *,
        stop_event: threading.Event | None = None,
        max_lag: float = 1.0,
        max_lead: float = 2.0,
    ) -> None:
        self._inner = inner
        self._stop = stop_event or threading.Event()
        self._frame_interval = 1.0 / (inner.native_fps or 25.0)
        self._max_lag = max_lag
        self._max_lead = max_lead
        self._anchor: float | None = None
        self._last_pts: float | None = None

    @property
    def native_fps(self) -> float | None:
        return self._inner.native_fps

    def read(self) -> Frame | None:
        frame = self._inner.read()
        if frame is None:
            return None
        pts = self._next_pts(self._inner.position_seconds)
        now = time.monotonic()
        due = now if self._anchor is None else self._anchor + pts
        if not -self._max_lag <= due - now <= self._max_lead:
            due = now
        self._anchor = due - pts
        delay = due - now
        if delay > 0 and self._stop.wait(delay):
            return None
        return frame

    def _next_pts(self, reported: float | None) -> float:
        last = self._last_pts
        if reported is None or (last is not None and last - 0.5 <= reported <= last):
            # No usable timestamp (or a repeated one): assume the native frame interval.
            pts = 0.0 if last is None else last + self._frame_interval
        else:
            pts = reported
        self._last_pts = pts
        return pts

    def close(self) -> None:
        self._inner.close()


def open_reader(
    source: ResolvedSource,
    *,
    timeout: float = 15.0,
    snapshot_interval: float = 1.0,
    stop_event: threading.Event | None = None,
) -> FrameReader:
    """Open a reader for a resolved source. ``stop_event`` interrupts throttling waits."""
    match source.kind:
        case ReaderKind.VIDEO:
            reader = VideoCaptureReader(source, open_timeout=timeout)
            return PacedReader(reader, stop_event=stop_event)
        case ReaderKind.MJPEG:
            return MjpegReader(source.uri, timeout=timeout)
        case ReaderKind.SNAPSHOT:
            return SnapshotReader(
                source.uri, timeout=timeout, min_interval=snapshot_interval, stop_event=stop_event
            )
