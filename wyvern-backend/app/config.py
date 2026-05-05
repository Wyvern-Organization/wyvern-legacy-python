from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Wyvern Backend"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    database_url: str = Field(validation_alias=AliasChoices("DATABASE_URL", "database_url"))
    redis_url: str = "redis://redis:6379/0"
    sync_peer_api_url: str | None = None
    node_id: str = Field(default="aspc", validation_alias=AliasChoices("WYVERN_NODE_ID", "NODE_ID"))
    indexing: bool = Field(default=False, validation_alias=AliasChoices("WYVERN_INDEXING", "INDEXING"))
    node_role: str = "main"
    sync_shared_secret: str | None = None
    sync_enabled: bool = False
    edge_mode_enabled: bool = False
    edge_handoff_ttl_seconds: int = 90
    sync_bridge_batch_size: int = 100
    sync_bridge_poll_interval_seconds: float = 2.0
    sync_bridge_request_timeout_seconds: float = 10.0
    sync_bridge_resync_interval_seconds: float = 300.0

    jwt_secret_key: str = Field(validation_alias=AliasChoices("JWT_SECRET_KEY", "jwt_secret_key"))
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    cors_origins: str = "http://localhost:8009,http://127.0.0.1:8009,http://localhost:3000,http://127.0.0.1:3000"
    mirror_target_url: str | None = None

    local_media_dir: str = "media"
    media_url_prefix: str = "/media"

    giphy_api_key: str | None = None
    giphy_rating: str = "g"
    giphy_limit: int = 24

    free_upload_limit_bytes: int = 25 * 1024 * 1024
    paid_upload_limit_bytes: int = 100 * 1024 * 1024

    admin_allowlist: str = ""

    rate_limit_message_count: int = 5
    rate_limit_message_window_seconds: int = 1
    rate_limit_upload_count: int = 10
    rate_limit_upload_window_seconds: int = 60
    rate_limit_auth_count: int = 10
    rate_limit_auth_window_seconds: int = 60
    rate_limit_webhook_count: int = 30
    rate_limit_webhook_window_seconds: int = 60

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
            return []
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("DATABASE_URL is required")
        return normalized

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("JWT_SECRET_KEY is required")
        if len(normalized) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return normalized

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
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MIRROR_TARGET_URL must be an http(s) URL")
        return normalized.rstrip("/")

    @field_validator("sync_peer_api_url", mode="before")
    @classmethod
    def normalize_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized.rstrip("/")

    @field_validator("giphy_api_key", mode="before")
    @classmethod
    def normalize_giphy_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("sync_shared_secret", mode="before")
    @classmethod
    def normalize_sync_shared_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("admin_allowlist", mode="before")
    @classmethod
    def normalize_admin_allowlist(cls, value: str | None) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("giphy_rating", mode="before")
    @classmethod
    def normalize_giphy_rating(cls, value: str) -> str:
        normalized = (value or "g").strip().lower()
        return normalized if normalized in {"g", "pg", "pg-13", "r"} else "g"

    @field_validator("giphy_limit", mode="before")
    @classmethod
    def normalize_giphy_limit(cls, value: int | str) -> int:
        try:
            normalized = int(value)
        except Exception:
            normalized = 24
        return max(1, min(50, normalized))

    @field_validator("node_role", mode="before")
    @classmethod
    def normalize_node_role(cls, value: str) -> str:
        normalized = (value or "main").strip().lower()
        return normalized if normalized in {"main", "edge"} else "main"

    @field_validator("node_id", mode="before")
    @classmethod
    def normalize_node_id(cls, value: str) -> str:
        normalized = (value or "aspc").strip().lower()
        return normalized if normalized in {"aspc", "nubu"} else "aspc"

    @field_validator("sync_enabled", "edge_mode_enabled", "indexing", mode="before")
    @classmethod
    def parse_bool_flag(cls, value: bool | str) -> bool:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    def resolve_media_dir(self, project_root: Path) -> Path:
        media_dir = Path(self.local_media_dir)
        if not media_dir.is_absolute():
            media_dir = project_root / media_dir
        return media_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
