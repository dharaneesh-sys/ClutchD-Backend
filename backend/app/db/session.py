import os
from collections.abc import AsyncGenerator
from urllib.parse import urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def _ensure_async_scheme(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def _resolve_db_url(raw_url: str) -> str:
    """Convert Render internal DB hostname to external, ensure async scheme."""
    parsed = urlparse(raw_url)
    host = parsed.hostname or ""
    if "render.com" in host or not host:
        return _ensure_async_scheme(raw_url)
    dpg_id = host.split(".")[0]
    region = os.environ.get("RENDER_INSTANCE_REGION", "oregon")
    external_host = f"{dpg_id}.{region}-postgres.render.com"
    port = parsed.port or 5432
    if parsed.password:
        netloc = f"{parsed.username}:{parsed.password}@{external_host}:{port}"
    elif parsed.username:
        netloc = f"{parsed.username}@{external_host}:{port}"
    else:
        netloc = f"{external_host}:{port}"
    new_url = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return _ensure_async_scheme(new_url)


_resolved_url: str | None = None
_engine = None
_SessionLocal = None


def _init_engine():
    global _resolved_url, _engine, _SessionLocal
    if _engine is not None:
        return
    settings = get_settings()
    _resolved_url = _resolve_db_url(settings.database_url)
    _engine = create_async_engine(
        _resolved_url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=300,
        pool_timeout=10,
        connect_args={"server_settings": {"statement_timeout": "30000"}},
    )
    _SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def get_resolved_url() -> str | None:
    return _resolved_url


def get_engine():
    _init_engine()
    return _engine


def reinit_engine(url: str | None = None):
    """Force-reinitialize the engine, optionally with a different URL."""
    global _resolved_url, _engine, _SessionLocal
    settings = get_settings()
    _resolved_url = url if url else _resolve_db_url(settings.database_url)
    _engine = create_async_engine(
        _resolved_url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=300,
        pool_timeout=10,
        connect_args={"server_settings": {"statement_timeout": "30000"}},
    )
    _SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def get_session_local():
    _init_engine()
    return _SessionLocal


class _LazySessionLocal:
    """Lazy proxy so ``from app.db.session import AsyncSessionLocal`` works.

    ``async with AsyncSessionLocal() as db:`` creates a session via the
    lazily-initialized sessionmaker, then enters its async context manager.
    """
    def __call__(self):
        maker = get_session_local()
        return maker()


AsyncSessionLocal: _LazySessionLocal = _LazySessionLocal()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session_local = get_session_local()
    async with session_local() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
