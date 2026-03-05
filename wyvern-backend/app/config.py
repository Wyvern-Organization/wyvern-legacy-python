from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Wyvern Backend"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/wyvern"
    redis_url: str = "redis://redis:6379/0"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    cors_origins: str = "*"
    mirror_target_url: str | None = None

    local_media_dir: str = "media"
    media_url_prefix: str = "/media"

    free_upload_limit_bytes: int = 1_073_741_824

    rate_limit_message_count: int = 5
    rate_limit_message_window_seconds: int = 1
    rate_limit_upload_count: int = 10
    rate_limit_upload_window_seconds: int = 60

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_flag(cls, value: bool | str) -> bool:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on", "debug", "development"}:
                return True
            if lowered in {"0", "false", "no", "off", "release", "prod", "production"}:
                return False
        return bool(value)

    def get_cors_origins(self) -> list[str]:
        if not self.cors_origins:
            return ["*"]
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @field_validator("media_url_prefix", mode="before")
    @classmethod
    def normalize_media_url_prefix(cls, value: str) -> str:
        if not value:
            return "/media"
        normalized = value.strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        normalized = normalized.rstrip("/")
        return normalized or "/media"

    @field_validator("mirror_target_url", mode="before")
    @classmethod
    def normalize_mirror_target_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized.rstrip("/")

    def resolve_media_dir(self, project_root: Path) -> Path:
        media_dir = Path(self.local_media_dir)
        if not media_dir.is_absolute():
            media_dir = project_root / media_dir
        return media_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
