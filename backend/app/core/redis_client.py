import redis.asyncio as redis

from app.core.config import get_settings

_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


async def redis_publish(channel: str, message: str) -> None:
    try:
        r = await get_redis()
        await r.publish(channel, message)
    except Exception:
        pass
