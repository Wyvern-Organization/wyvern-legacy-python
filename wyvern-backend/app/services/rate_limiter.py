from fastapi import HTTPException, status

from app.services.redis_client import get_redis


class RateLimiter:
    async def check(self, key_prefix: str, actor_id: int, limit: int, window_seconds: int) -> None:
        redis = get_redis()
        key = f"rl:{key_prefix}:{actor_id}:{window_seconds}"
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window_seconds)
        if current > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {key_prefix}",
            )


rate_limiter = RateLimiter()
