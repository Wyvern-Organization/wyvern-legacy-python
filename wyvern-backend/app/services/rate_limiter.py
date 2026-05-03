from fastapi import HTTPException, status

from app.services.redis_client import get_redis


RATE_LIMIT_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("EXPIRE", KEYS[1], tonumber(ARGV[1]))
end
return current
"""


class RateLimiter:
    async def check(self, key_prefix: str, actor_id: int | str, limit: int, window_seconds: int) -> None:
        redis = get_redis()
        actor_key = str(actor_id).strip() or "anonymous"
        key = f"rl:{key_prefix}:{actor_key}:{window_seconds}"
        current = int(await redis.eval(RATE_LIMIT_SCRIPT, 1, key, int(window_seconds)))
        if current > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {key_prefix}",
            )


rate_limiter = RateLimiter()
