from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://audio:local-audio-password@db:5432/audio"
    redis_url: str = "redis://redis:6379/0"
    storage_root: Path = Path("/data")
    debug: bool = False
    max_upload_mb: int = Field(250, ge=1, le=2000)
    max_duration_seconds: int = Field(21600, ge=1, le=86400)
    storage_quota_gb: float = Field(20, gt=0)
    process_timeout_seconds: int = Field(3600, ge=5, le=43200)
    preview_ttl_hours: int = Field(24, ge=1)
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    allowed_origins: str = (
        "http://localhost:8000,http://127.0.0.1:8000,http://localhost:8080,http://127.0.0.1:8080"
    )


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
