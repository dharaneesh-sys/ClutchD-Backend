import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_TOKEN_ISSUER = "clutchd"
_TOKEN_AUDIENCE = "clutchd-api"

# Known weak/default secrets that should never be used in production
_KNOWN_WEAK_SECRETS = {
    "change-me-in-production-use-openssl-rand-hex-32",
}

# Hashes of known weak secrets (catches copies/modifications)
_KNOWN_WEAK_HASHES: set[str] = {
    hashlib.sha256(s.encode()).hexdigest()
    for s in _KNOWN_WEAK_SECRETS
}


def _check_secret() -> str:
    """Return the JWT secret, raising immediately if weak/default in production."""
    settings = get_settings()
    secret = settings.jwt_secret_key

    if not settings.debug:
        # Check against known weak values
        if secret in _KNOWN_WEAK_SECRETS:
            raise RuntimeError(
                "FATAL: jwt_secret_key is set to a known default value. "
                "Set a strong secret via the JWT_SECRET_KEY environment variable."
            )
        # Check against known weak hashes
        if hashlib.sha256(secret.encode()).hexdigest() in _KNOWN_WEAK_HASHES:
            raise RuntimeError(
                "FATAL: jwt_secret_key matches a known weak secret. "
                "Generate a strong secret with: "
                "python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        # Warn if secret looks weak (too short)
        if len(secret) < 32:
            logger.warning(
                "WARNING: jwt_secret_key is only %d characters long. "
                "Use at least 64 characters (32 bytes hex) in production.",
                len(secret),
            )

    return secret


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    secret = _check_secret()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iss": _TOKEN_ISSUER,
        "aud": _TOKEN_AUDIENCE,
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> str:
    settings = get_settings()
    secret = _check_secret()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iss": _TOKEN_ISSUER,
        "aud": _TOKEN_AUDIENCE,
        "type": "refresh",
    }
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any] | None:
    """Decode and validate a JWT token. Returns claims dict or None on failure."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=_TOKEN_ISSUER,
            audience=_TOKEN_AUDIENCE,
        )
    except JWTError:
        return None

    # Verify token type
    if payload.get("type") != expected_type:
        return None

    return payload


async def is_token_blacklisted(token_jti: str) -> bool:
    """Check if a token has been revoked. Fails closed if Redis is unavailable."""
    try:
        from app.core.redis_client import get_redis
        r = await get_redis()
        return bool(await r.sismember("token_blacklist", token_jti))
    except Exception:
        # If Redis is down, fail closed (reject the token to be safe)
        logger.warning("Redis unavailable for token blacklist check — rejecting token")
        return True


async def blacklist_token(token_jti: str, ttl_seconds: int = 86400 * 8) -> None:
    """Add a token to the blacklist."""
    try:
        from app.core.redis_client import get_redis
        r = await get_redis()
        await r.sadd("token_blacklist", token_jti)
        # Auto-expire old entries (clean up)
        await r.expire("token_blacklist", ttl_seconds)
    except Exception:
        logger.warning("Failed to blacklist token in Redis")
