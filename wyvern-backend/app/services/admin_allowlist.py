import re
from collections.abc import Callable

from app.config import Settings, get_settings


ALLOWLIST_SPLIT_RE = re.compile(r"[\s,]+")


class AdminAllowlistService:
    def __init__(self, settings_provider: Callable[[], Settings] = get_settings) -> None:
        self._settings_provider = settings_provider

    @staticmethod
    def _normalize_handle(value: str) -> str:
        handle = value.strip()
        if "#" not in handle:
            return ""
        username, discriminator = handle.rsplit("#", 1)
        username = username.strip().lower()
        discriminator = discriminator.strip()
        if not username or not discriminator:
            return ""
        return f"{username}#{discriminator}"

    @classmethod
    def parse_allowlist(cls, value: str | None) -> set[str]:
        if not value:
            return set()
        return {
            normalized
            for normalized in (cls._normalize_handle(item) for item in ALLOWLIST_SPLIT_RE.split(value))
            if normalized
        }

    async def get_allowlist(self) -> set[str]:
        return self.parse_allowlist(self._settings_provider().admin_allowlist)

    async def is_admin(self, username: str | None, discriminator: str | None = None) -> bool:
        if not username or not discriminator:
            return False
        allowlist = await self.get_allowlist()
        if not allowlist:
            return False
        return self._normalize_handle(f"{username}#{discriminator}") in allowlist


admin_allowlist_service = AdminAllowlistService()
