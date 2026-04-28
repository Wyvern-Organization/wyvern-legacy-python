from app.models.enums import PresenceStatus
from app.services.redis_client import get_redis


class PresenceService:
    async def set_presence(self, user_id: str, status: PresenceStatus | str) -> PresenceStatus:
        redis = get_redis()
        normalized = PresenceStatus(status)
        key = f"presence:{user_id}"
        await redis.set(key, normalized.value, ex=300)
        return normalized

    async def get_presence(self, user_id: str) -> PresenceStatus:
        redis = get_redis()
        value = await redis.get(f"presence:{user_id}")
        if value is None:
            return PresenceStatus.offline
        try:
            return PresenceStatus(value)
        except ValueError:
            return PresenceStatus.offline


presence_service = PresenceService()
