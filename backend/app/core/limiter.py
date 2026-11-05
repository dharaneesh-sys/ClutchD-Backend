from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.core.security import decode_token

settings = get_settings()


def rate_limit_key(request: Request) -> str:
    """Bucket per user, not per proxy.

    Every device reaches the API through the Tailscale funnel, so
    get_remote_address() is the funnel for ALL users and every 10-15/min limit
    became one global bucket — a few retries locked everyone out of login,
    jobs and vehicles (429 "Too many requests").

    Order: valid access-token subject -> client IP appended by our trusted
    proxy (LAST X-Forwarded-For hop; earlier hops are client-controlled and
    spoofable) -> socket address.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = decode_token(auth[7:].strip())
        if payload and payload.get("sub"):
            return f"user:{payload['sub']}"
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return f"ip:{last}"
    return get_remote_address(request)


limiter = Limiter(
    key_func=rate_limit_key,
    storage_uri=settings.redis_url,
)
