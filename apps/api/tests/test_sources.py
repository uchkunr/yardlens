import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from yardlens_api.core.enums import SourceType
from yardlens_api.core.errors import SourceError
from yardlens_api.services.sources import (
    Frame,
    PacedReader,
    ReaderKind,
    VideoCaptureReader,
    iter_jpeg_frames,
    open_reader,
    resolve_file_path,
    resolve_source,
    validate_source,
)


@pytest.mark.parametrize(
    ("source_type", "url"),
    [
        (SourceType.RTSP, "rtsp://camera.local:554/stream1"),
        (SourceType.RTSP, "rtsps://camera.local/live"),
        (SourceType.HTTP, "https://example.com/snapshot.jpg"),
        (SourceType.HLS, "https://example.com/live/playlist.m3u8"),
        (SourceType.YOUTUBE, "https://www.youtube.com/watch?v=abc123"),
        (SourceType.YOUTUBE, "https://youtu.be/abc123"),
    ],
)
def test_validate_accepts_supported_urls(source_type: SourceType, url: str) -> None:
    validate_source(source_type, url)


@pytest.mark.parametrize(
    ("source_type", "url", "message"),
    [
        (SourceType.RTSP, "http://camera.local/stream", "RTSP URL must start with"),
        (SourceType.HTTP, "ftp://example.com/image.jpg", "HTTP URL must start with"),
        (SourceType.HLS, "playlist.m3u8", "HLS URL must start with"),
        (SourceType.YOUTUBE, "https://vimeo.com/123", "youtube.com or youtu.be"),
        (SourceType.FILE, "videos/missing.mp4", "Video file not found"),
    ],
)
def test_validate_rejects_invalid_urls(source_type: SourceType, url: str, message: str) -> None:
    with pytest.raises(SourceError, match=message):
        validate_source(source_type, url)


def test_file_paths_resolve_relative_to_base_dir(tmp_path: Path) -> None:
    assert resolve_file_path("videos/a.mp4", base_dir=tmp_path) == tmp_path / "videos/a.mp4"
    # as_uri() is file:///C:/... on Windows and file:///tmp/... elsewhere.
    clip = tmp_path / "clips" / "a b.mp4"
    assert resolve_file_path(clip.as_uri(), base_dir=Path("elsewhere")) == clip


def test_resolve_youtube_uses_resolver() -> None:
    resolved = resolve_source(
        SourceType.YOUTUBE,
        "https://www.youtube.com/watch?v=live",
        youtube_resolver=lambda url: f"https://cdn.example/{url.rsplit('=', 1)[1]}.m3u8",
    )
    assert resolved.uri == "https://cdn.example/live.m3u8"
    assert resolved.kind is ReaderKind.VIDEO
    assert not resolved.loop


def test_resolve_youtube_propagates_resolver_errors() -> None:
    def failing(_: str) -> str:
        raise SourceError("stream is offline")

    with pytest.raises(SourceError, match="stream is offline"):
        resolve_source(SourceType.YOUTUBE, "https://youtu.be/x", youtube_resolver=failing)


@pytest.mark.parametrize("kind", [ReaderKind.MJPEG, ReaderKind.SNAPSHOT])
def test_resolve_http_uses_probe(kind: ReaderKind) -> None:
    resolved = resolve_source(
        SourceType.HTTP, "https://example.com/cam", http_prober=lambda _: kind
    )
    assert resolved.kind is kind


def test_resolve_file_loops(video_file: Path) -> None:
    resolved = resolve_source(SourceType.FILE, "videos/clip.mp4", base_dir=video_file.parent.parent)
    assert resolved.loop
    assert resolved.uri == str(video_file.resolve())


def test_file_reader_loops_past_the_end(video_file: Path) -> None:
    resolved = resolve_source(SourceType.FILE, str(video_file))
    reader = VideoCaptureReader(resolved)
    try:
        frames = [reader.read() for _ in range(30)]
    finally:
        reader.close()
    assert all(frame is not None for frame in frames)
    first = frames[0]
    assert first is not None
    assert first.shape == (120, 160, 3)


def test_frames_can_be_skipped_without_converting_them(video_file: Path) -> None:
    resolved = resolve_source(SourceType.FILE, str(video_file))
    # Asking for hardware decoding is safe anywhere: OpenCV falls back to the CPU.
    reader = VideoCaptureReader(resolved, hardware_decode=True, threads=1)
    try:
        assert all(reader.grab() for _ in range(20)), "looped files never run out"
        frame = reader.retrieve()
        assert frame is not None
        assert frame.shape == (120, 160, 3)
    finally:
        reader.close()


def test_iter_jpeg_frames_handles_split_chunks_and_noise() -> None:
    first = b"\xff\xd8first-image\xff\xd9"
    second = b"\xff\xd8second\xff\xd9"
    stream = (
        b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n" + first + b"\r\n--boundary\r\n" + second
    )
    chunks = [stream[index : index + 7] for index in range(0, len(stream), 7)]
    assert list(iter_jpeg_frames(iter(chunks))) == [first, second]


def test_iter_jpeg_frames_ignores_incomplete_trailing_frame() -> None:
    assert list(iter_jpeg_frames(iter([b"\xff\xd8partial"]))) == []


def test_open_reader_paces_video_files(video_file: Path) -> None:
    reader = open_reader(resolve_source(SourceType.FILE, str(video_file)))
    try:
        assert isinstance(reader, PacedReader)
        started = time.monotonic()
        for _ in range(4):
            assert reader.read() is not None
        # The clip is 10 fps: four frames span three intervals.
        assert time.monotonic() - started >= 0.25
    finally:
        reader.close()


class _TimedFrames:
    """Fake video reader returning frames with scripted presentation timestamps."""

    def __init__(self, timestamps: Sequence[float | None], fps: float | None = 50.0) -> None:
        self._timestamps = list(timestamps)
        self._fps = fps
        self._index = -1

    @property
    def native_fps(self) -> float | None:
        return self._fps

    @property
    def hardware_decoding(self) -> bool:
        return False

    @property
    def position_seconds(self) -> float | None:
        return self._timestamps[self._index]

    def grab(self) -> bool:
        self._index += 1
        return self._index < len(self._timestamps)

    def retrieve(self) -> Frame | None:
        return np.zeros((2, 2, 3), dtype=np.uint8)

    def read(self) -> Frame | None:
        return self.retrieve() if self.grab() else None

    def close(self) -> None:
        return None


def _read_all(reader: PacedReader) -> float:
    started = time.monotonic()
    while reader.read() is not None:
        pass
    return time.monotonic() - started


def test_paced_reader_follows_timestamps() -> None:
    elapsed = _read_all(PacedReader(_TimedFrames([0.0, 0.1, 0.2, 0.3])))
    assert 0.27 <= elapsed < 0.6


def test_paced_reader_spreads_a_burst_at_real_time() -> None:
    # An HLS segment arrives at once; frames must still be released 0.1 s apart.
    elapsed = _read_all(PacedReader(_TimedFrames([10.0, 10.1, 10.2])))
    assert elapsed >= 0.18


def test_paced_reader_reanchors_on_timestamp_jumps() -> None:
    # A looped file restarts at 0 and a live stream jumps ahead: neither should stall.
    elapsed = _read_all(PacedReader(_TimedFrames([5.0, 5.05, 0.0, 60.0])))
    assert elapsed < 0.2


def test_paced_reader_uses_native_fps_without_timestamps() -> None:
    elapsed = _read_all(PacedReader(_TimedFrames([None, None, None], fps=20.0)))
    assert 0.09 <= elapsed < 0.4
