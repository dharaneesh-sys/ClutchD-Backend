import os
from collections.abc import AsyncGenerator
from urllib.parse import urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def _resolve_db_url(raw_url: str) -> str:
    """Convert Render internal DB hostname to external if needed.

    Render's ``fromDatabase`` connectionString uses an internal hostname
    such as ``dpg-xxxxx.internal`` which is unreachable on the free plan
    (no private networking).  This converts it to the public equivalent.
    """
    parsed = urlparse(raw_url)
    host = parsed.hostname or ""
    if "render.com" in host or ".internal" not in host:
        return raw_url
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
    if new_url.startswith("postgresql://"):
        new_url = new_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return new_url


settings = get_settings()
resolved_url = _resolve_db_url(settings.database_url)
engine = create_async_engine(
    resolved_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=300,
    pool_timeout=30,
    connect_args={"server_settings": {"statement_timeout": "30000"}},  # 30s max query time
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
