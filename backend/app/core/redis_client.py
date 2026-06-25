import redis.asyncio as redis

from app.core.config import get_settings

_client: redis.Redis | None = None


async def get_redis() -> redis.Redis | None:
    global _client
    if _client is None:
        settings = get_settings()
        try:
            _client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                password=settings.redis_password,
            )
            # Verify the connection actually works
            await _client.ping()
        except Exception:
            _client = None  # Redis unavailable — callers should handle None
    return _client


async def redis_publish(channel: str, message: str) -> None:
    try:
        r = await get_redis()
        await r.publish(channel, message)
    except Exception:
        pass
