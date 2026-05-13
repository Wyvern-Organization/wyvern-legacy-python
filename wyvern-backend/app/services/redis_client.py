from redis.asyncio import Redis
import redis.asyncio as redis

from app.config import get_settings


_redis_client: Redis | None = None


async def init_redis() -> None:
    global _redis_client
    settings = get_settings()
    _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    await _redis_client.ping()


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None


def get_redis() -> Redis:
    if _redis_client is None:
        raise RuntimeError("Redis client is not initialized")
    return _redis_client
