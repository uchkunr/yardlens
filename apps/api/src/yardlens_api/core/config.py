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
    port: int = 8100
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False

    data_dir: Path = Path("data")
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/yardlens"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3100", "http://127.0.0.1:3100"]
    )

    default_model: str = "yolo26n.pt"
    # auto | cpu | cuda:0 | mps | intel:cpu | intel:gpu
    inference_device: str = "auto"
    # auto: OpenVINO on the CPU, PyTorch on GPUs | pytorch | openvino
    inference_backend: Literal["auto", "pytorch", "openvino"] = "auto"
    # Threads for inference on the CPU; 0 picks them from the number of cores.
    inference_threads: int = Field(default=0, ge=0, le=64)
    # Decode camera video on the GPU (Intel Quick Sync, NVIDIA, AMD) where possible.
    hardware_decode: bool = True
    # Run below normal priority so the OS, database and web app stay responsive under load.
    low_priority: bool = True
    workers_enabled: bool = True

    stream_jpeg_quality: int = Field(default=80, ge=30, le=95)
    snapshot_max_width: int = Field(default=960, ge=320)
    max_frame_width: int = Field(default=1280, ge=320)
    stream_fps: float = Field(default=15.0, ge=1.0, le=30.0)
    event_retention_days: int = Field(default=7, ge=1)
    # Photos of loading sessions are kept this long; the sessions themselves stay for good.
    session_image_retention_days: int = Field(default=365, ge=1)
    # Loading bays: seconds a vehicle stands in the zone before it counts as arrived, and seconds
    # with neither a vehicle nor cargo before it counts as gone.
    loading_arrive_seconds: float = Field(default=15.0, ge=1.0)
    loading_depart_seconds: float = Field(default=60.0, ge=5.0)

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
