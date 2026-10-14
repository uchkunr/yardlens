from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """The worker's configuration, from environment variables (prefix ``YARDLENS_``). The
    dashboard's "Add worker" command sets the first three."""

    model_config = SettingsConfigDict(env_prefix="YARDLENS_", extra="ignore")

    server: str = Field(description="The YardLens server, e.g. https://yardlens.local")
    worker_token: SecretStr
    ca_pin: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of the server's certificate authority: the worker "
        "trusts that CA only, so it works with the server's own HTTPS certificates",
    )
    data_dir: Path = Path("worker-data")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False
    default_model: str = "yolo26n.pt"
    inference_device: str = "auto"
    inference_backend: Literal["auto", "pytorch", "openvino"] = "auto"
    inference_threads: int = Field(default=0, ge=0, le=64)
    hardware_decode: bool = True
    low_priority: bool = True
    stream_jpeg_quality: int = Field(default=80, ge=30, le=95)
    snapshot_max_width: int = Field(default=960, ge=320)
    max_frame_width: int = Field(default=1280, ge=320)
    stream_fps: float = Field(default=15.0, ge=1.0, le=30.0)
    loading_arrive_seconds: float = Field(default=15.0, ge=1.0)
    loading_depart_seconds: float = Field(default=60.0, ge=5.0)

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.snapshots_dir, self.models_dir):
            directory.mkdir(parents=True, exist_ok=True)
