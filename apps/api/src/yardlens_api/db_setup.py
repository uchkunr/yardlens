"""Create the PostgreSQL database if missing and apply ``db/schema.sql`` (``uv run db-setup``).

Talks to the server through SQLAlchemy and asyncpg, like the API itself, so it behaves the same on
Windows, macOS and Linux and needs no PostgreSQL client tools on the ``PATH``.
"""

import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from yardlens_api.core.config import get_settings
from yardlens_api.core.logging import configure_logging

logger = logging.getLogger("yardlens_api.db_setup")

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


async def ensure_database(url: URL) -> bool:
    """Create ``url.database`` through the ``postgres`` maintenance database. True if created."""
    database = url.database
    if not database:
        raise ValueError("YARDLENS_DATABASE_URL does not name a database")
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database}
            )
            if exists:
                return False
            quoted = connection.dialect.identifier_preparer.quote_identifier(database)
            # template0 records the server's current collation version, so the new database
            # doesn't inherit a stale one from template1 after an OS or ICU upgrade.
            await connection.execute(text(f"CREATE DATABASE {quoted} TEMPLATE template0"))
            return True
    finally:
        await admin.dispose()


async def apply_schema(url: URL, schema: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None
            # asyncpg runs a multi-statement script in one implicit transaction: all or nothing.
            await driver.execute(schema)
    finally:
        await engine.dispose()


async def setup(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        logger.info("Database is not PostgreSQL; nothing to do", extra={"backend": url.drivername})
        return
    if await ensure_database(url):
        logger.info("Created database", extra={"database": url.database})
    await apply_schema(url, SCHEMA_PATH.read_text(encoding="utf-8"))
    logger.info("Schema applied", extra={"database": url.database})


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    try:
        asyncio.run(setup(settings.database_url))
    except (OSError, SQLAlchemyError, ValueError) as exc:
        logger.error("Database setup failed: %s", exc)
        sys.exit(1)
