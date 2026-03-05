import asyncio
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ADMINS_FILE = PROJECT_ROOT / "admins.json"


class AdminAllowlistService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cached_mtime: float | None = None
        self._cached_usernames: set[str] = set()

    @staticmethod
    def _normalize_username(value: str) -> str:
        return value.strip().lower()

    @staticmethod
    def _extract_usernames(payload: object) -> set[str]:
        usernames: list[str] = []
        if isinstance(payload, list):
            usernames = [item for item in payload if isinstance(item, str)]
        elif isinstance(payload, dict):
            raw = payload.get("usernames")
            if isinstance(raw, list):
                usernames = [item for item in raw if isinstance(item, str)]

        normalized = {
            AdminAllowlistService._normalize_username(username)
            for username in usernames
            if username and AdminAllowlistService._normalize_username(username)
        }
        return normalized

    async def _load_allowlist_from_disk(self) -> set[str]:
        try:
            content = await asyncio.to_thread(ADMINS_FILE.read_text, encoding="utf-8")
        except FileNotFoundError:
            return set()
        except OSError:
            return set()

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return set()

        return self._extract_usernames(parsed)

    async def get_allowlist(self) -> set[str]:
        try:
            stat = await asyncio.to_thread(ADMINS_FILE.stat)
            mtime = stat.st_mtime
        except FileNotFoundError:
            self._cached_mtime = None
            self._cached_usernames = set()
            return set()
        except OSError:
            return set(self._cached_usernames)

        if self._cached_mtime == mtime:
            return set(self._cached_usernames)

        async with self._lock:
            # Re-check inside lock so parallel calls do not reload twice.
            if self._cached_mtime == mtime:
                return set(self._cached_usernames)

            usernames = await self._load_allowlist_from_disk()
            self._cached_mtime = mtime
            self._cached_usernames = usernames
            return set(usernames)

    async def is_admin(self, username: str | None, discriminator: str | None = None) -> bool:
        if not username:
            return False
        allowlist = await self.get_allowlist()
        normalized_username = self._normalize_username(username)
        candidates = {normalized_username}
        if discriminator:
            normalized_discriminator = discriminator.strip()
            if normalized_discriminator:
                candidates.add(f"{normalized_username}#{normalized_discriminator}")
        return any(candidate in allowlist for candidate in candidates)


admin_allowlist_service = AdminAllowlistService()
