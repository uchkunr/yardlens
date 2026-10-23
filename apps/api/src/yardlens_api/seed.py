"""Seed demo cameras (``uv run seed``).

Inserts two looped sample videos: a highway with a counting line, which counts vehicles with the
stock model straight away, and a loading bay with its zone and line, which counts cargo once a
model trained on it is in place (see docs/sack-model.md). Runs once per database: a marker in
``app_settings`` remembers it, so demo cameras you delete stay deleted even when ``bun run setup``
runs again. A database that already has cameras is never seeded. Pass ``--force`` to add any
missing demo cameras regardless.
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from yardlens_api.core.config import Settings, get_settings
from yardlens_api.core.enums import SourceType
from yardlens_api.core.logging import configure_logging
from yardlens_api.db.models import AppSetting, Camera
from yardlens_api.db.session import create_engine, create_session_factory, init_models
from yardlens_api.schemas.camera import CountingLine, LoadingZone, ZonePoint

logger = logging.getLogger("yardlens_api.seed")

SEEDED_KEY = "demo_seeded"

# Free sample footage: supervision's traffic clip, and a Pexels clip (free licence, by emirkhan
# bal) of sacks being loaded onto a trailer.
SAMPLE_VIDEOS: dict[str, str] = {
    "vehicles.mp4": "https://media.roboflow.com/supervision/video-examples/vehicles.mp4",
    "loading-bay.mp4": (
        "https://videos.pexels.com/video-files/11324000/11324000-hd_1920_1080_30fps.mp4"
    ),
}


@dataclass(frozen=True, slots=True)
class DemoCamera:
    name: str
    video: str
    inference_fps: float
    line: CountingLine
    zone: LoadingZone | None = None


DEMO_CAMERAS: tuple[DemoCamera, ...] = (
    DemoCamera(
        name="Sample · Highway (looped video)",
        video="vehicles.mp4",
        inference_fps=8.0,
        line=CountingLine(x1=0.0, y1=0.62, x2=1.0, y2=0.62),
    ),
    DemoCamera(
        name="Sample · Loading bay (looped video)",
        video="loading-bay.mp4",
        inference_fps=10.0,
        line=CountingLine(x1=0.36, y1=0.30, x2=0.46, y2=0.62),
        zone=LoadingZone(
            points=[
                ZonePoint(x=0.2, y=0.75),
                ZonePoint(x=1.0, y=0.75),
                ZonePoint(x=1.0, y=1.0),
                ZonePoint(x=0.2, y=1.0),
            ]
        ),
    ),
)


def _seeded_marker() -> AppSetting:
    return AppSetting(key=SEEDED_KEY, value={"seeded_at": datetime.now(UTC).isoformat()})


def download_video(settings: Settings, name: str) -> bool:
    target = settings.videos_dir / name
    if target.is_file() and target.stat().st_size > 0:
        return True
    url = SAMPLE_VIDEOS[name]
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    logger.info("Downloading sample video", extra={"url": url})
    try:
        with (
            httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response,
            partial.open("wb") as file,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=1 << 16):
                file.write(chunk)
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        logger.warning("Sample video download failed", extra={"url": url, "error": str(exc)})
        return False
    partial.rename(target)
    return True


async def seed(settings: Settings, *, force: bool) -> int:
    engine = create_engine(settings.database_url)
    try:
        await init_models(engine)
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            seeded = await session.get(AppSetting, SEEDED_KEY)
            count = await session.scalar(select(func.count()).select_from(Camera)) or 0
            if not force and (seeded is not None or count):
                if seeded is None:
                    session.add(_seeded_marker())
                    await session.commit()
                logger.info("Demo cameras were seeded before or cameras exist; skipping seed")
                return 0
            existing = set((await session.scalars(select(Camera.name))).all())

            added = 0
            for demo in DEMO_CAMERAS:
                if demo.name in existing:
                    continue
                if not await asyncio.to_thread(download_video, settings, demo.video):
                    continue
                session.add(
                    Camera(
                        name=demo.name,
                        source_type=SourceType.FILE.value,
                        url=f"videos/{demo.video}",
                        enabled=True,
                        inference_fps=demo.inference_fps,
                        line=demo.line.model_dump(),
                        zone=demo.zone.model_dump() if demo.zone else None,
                    )
                )
                added += 1
            if seeded is None:
                session.add(_seeded_marker())
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
