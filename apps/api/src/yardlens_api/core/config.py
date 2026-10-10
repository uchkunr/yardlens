from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level configuration loaded from environment variables (prefix ``YARDLENS_``)."""

    model_config = SettingsConfigDict(
        env_prefix="YARDLENS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "production", "test"] = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False

    data_dir: Path = Path("data")
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/yardlens"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    default_model: str = "yolo26n.pt"
    inference_device: str = "auto"
    workers_enabled: bool = True

    stream_jpeg_quality: int = Field(default=80, ge=30, le=95)
    snapshot_max_width: int = Field(default=960, ge=320)
    max_frame_width: int = Field(default=1280, ge=320)
    stream_fps: float = Field(default=15.0, ge=1.0, le=30.0)
    event_retention_days: int = Field(default=7, ge=1)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def videos_dir(self) -> Path:
        return self.data_dir / "videos"

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.snapshots_dir, self.models_dir, self.videos_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
