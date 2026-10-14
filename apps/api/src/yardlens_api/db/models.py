from datetime import UTC, datetime
from typing import Any, ClassVar, override

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

# The canonical PostgreSQL schema lives in db/schema.sql (applied by db-setup). These mappings match
# it; ``metadata.create_all`` is only used for SQLite (tests and quick local runs).
JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores timezone-aware datetimes as UTC and always returns aware values (SQLite drops tz)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    @override
    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use timezone-aware values")
        return value.astimezone(UTC)

    @override
    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime(),
        dict[str, Any]: JsonType,
    }


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    source_type: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(2048))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    inference_fps: Mapped[float] = mapped_column(Float, default=3.0)
    line: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    zone: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    events: Mapped[list[Event]] = relationship(
        back_populates="camera", cascade="all, delete-orphan", passive_deletes=True
    )
    loading_sessions: Mapped[list[LoadingSession]] = relationship(
        back_populates="camera", cascade="all, delete-orphan", passive_deletes=True
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_created_at", "created_at"),
        Index("ix_events_camera_created", "camera_id", "created_at"),
        Index("ix_events_type_created", "type", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
    )
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))
    class_name: Mapped[str | None] = mapped_column(String(64), default=None)
    tracker_id: Mapped[int | None] = mapped_column(Integer, default=None)
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    snapshot_path: Mapped[str | None] = mapped_column(String(512), default=None)
    details: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    camera: Mapped[Camera] = relationship(back_populates="events")


class LoadingSession(Base):
    """A vehicle loaded in a bay: when it came and left, how much cargo moved, its plate."""

    __tablename__ = "loading_sessions"
    __table_args__ = (
        Index("ix_loading_sessions_arrived", "arrived_at"),
        Index("ix_loading_sessions_camera_arrived", "camera_id", "arrived_at"),
        Index("ix_loading_sessions_plate", "plate"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    arrived_at: Mapped[datetime] = mapped_column()
    departed_at: Mapped[datetime | None] = mapped_column(default=None)
    loading_started_at: Mapped[datetime | None] = mapped_column(default=None)
    loading_finished_at: Mapped[datetime | None] = mapped_column(default=None)
    loaded: Mapped[int] = mapped_column(Integer, default=0)
    unloaded: Mapped[int] = mapped_column(Integer, default=0)
    vehicle_class: Mapped[str | None] = mapped_column(String(64), default=None)
    plate: Mapped[str | None] = mapped_column(String(16), default=None)
    plate_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    plate_corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    vehicle_snapshot_path: Mapped[str | None] = mapped_column(String(512), default=None)
    plate_snapshot_path: Mapped[str | None] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    camera: Mapped[Camera] = relationship(back_populates="loading_sessions")


class AppSetting(Base):
    """Key/value store for runtime-editable settings (detection, integrations)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
