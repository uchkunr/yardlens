"""Seed demo cameras (``uv run seed``).

Inserts a looped sample traffic video plus a few cameras that their operators publish for public
viewing. Runs only when the cameras table is empty, so it never re-adds cameras you deleted;
pass ``--force`` to add any missing demo cameras regardless.
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass

import httpx
from sqlalchemy import func, select

from yardlens_api.core.config import Settings, get_settings
from yardlens_api.core.enums import SourceType
from yardlens_api.core.logging import configure_logging
from yardlens_api.db.models import Camera
from yardlens_api.db.session import create_engine, create_session_factory, init_models
from yardlens_api.schemas.camera import CountingLine

logger = logging.getLogger("yardlens_api.seed")

SAMPLE_VIDEO_URL = "https://media.roboflow.com/supervision/video-examples/vehicles.mp4"
SAMPLE_VIDEO_NAME = "vehicles.mp4"


@dataclass(frozen=True, slots=True)
class DemoCamera:
    name: str
    source_type: SourceType
    url: str
    inference_fps: float = 3.0
    line: CountingLine | None = None


# The public cameras are official New York State DOT traffic cameras, published for public
# viewing through the 511NY traveler service (list: nysdottg.carsprogram.org/cameras_v1/api).
DEMO_CAMERAS: tuple[DemoCamera, ...] = (
    DemoCamera(
        name="Sample · Highway (looped video)",
        source_type=SourceType.FILE,
        url=f"videos/{SAMPLE_VIDEO_NAME}",
        inference_fps=8.0,
        line=CountingLine(x1=0.0, y1=0.62, x2=1.0, y2=0.62),
    ),
    DemoCamera(
        name="NYSDOT · East Ridge Rd at Culver Rd (Rochester)",
        source_type=SourceType.HLS,
        url="https://s7.nysdot.skyvdn.com/rtplive/R4_262/playlist.m3u8",
    ),
    DemoCamera(
        name="NYSDOT · NY 25 at NY 25A (Long Island)",
        source_type=SourceType.HLS,
        url="https://s7.nysdot.skyvdn.com/rtplive/R10_412/playlist.m3u8",
    ),
    DemoCamera(
        name="NYSDOT · I-278 BQE at 58 St (Queens)",
        source_type=SourceType.HLS,
        url="https://s9.nysdot.skyvdn.com/rtplive/R11_262/playlist.m3u8",
    ),
)


def download_sample_video(settings: Settings) -> bool:
    target = settings.videos_dir / SAMPLE_VIDEO_NAME
    if target.is_file() and target.stat().st_size > 0:
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    logger.info("Downloading sample video", extra={"url": SAMPLE_VIDEO_URL})
    try:
        with (
            httpx.stream("GET", SAMPLE_VIDEO_URL, follow_redirects=True, timeout=60.0) as response,
            partial.open("wb") as file,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=1 << 16):
                file.write(chunk)
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        logger.warning("Sample video download failed", extra={"error": str(exc)})
        return False
    partial.rename(target)
    return True


async def seed(settings: Settings, *, force: bool) -> int:
    engine = create_engine(settings.database_url)
    try:
        await init_models(engine)
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(Camera)) or 0
            if count and not force:
                logger.info("Cameras already configured; skipping seed", extra={"count": count})
                return 0
            existing = set((await session.scalars(select(Camera.name))).all())

            has_video = await asyncio.to_thread(download_sample_video, settings)
            added = 0
            for demo in DEMO_CAMERAS:
                if demo.name in existing:
                    continue
                if demo.source_type == SourceType.FILE and not has_video:
                    continue
                session.add(
                    Camera(
                        name=demo.name,
                        source_type=demo.source_type.value,
                        url=demo.url,
                        enabled=True,
                        inference_fps=demo.inference_fps,
                        line=demo.line.model_dump() if demo.line else None,
                    )
                )
                added += 1
            await session.commit()
    finally:
        await engine.dispose()
    logger.info("Seed complete", extra={"added": added})
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Insert YardLens demo cameras.")
    parser.add_argument(
        "--force", action="store_true", help="add missing demo cameras even if others exist"
    )
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    settings.ensure_directories()
    try:
        asyncio.run(seed(settings, force=bool(args.force)))
    except OSError as exc:
        logger.error("Could not connect to the database: %s", exc)
        sys.exit(1)
