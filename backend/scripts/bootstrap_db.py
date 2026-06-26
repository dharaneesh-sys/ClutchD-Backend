#!/usr/bin/env python3
"""Create PostGIS extension, SQLAlchemy tables, and seed demo data."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_engine, get_session_local
from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User


def _build_external_db_url() -> str | None:
    """If DATABASE_URL uses an internal Render hostname, construct the external .render.com URL.

    Render's ``fromDatabase`` connectionString uses an internal hostname such as
    ``dpg-xxxxx.internal`` which is unreachable on the free plan (no private
    networking).  This helper converts it to the public equivalent using the
    region suffix.

    Returns the new URL (with ``+asyncpg`` driver) or ``None`` if no conversion
    is needed.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None

    parsed = urlparse(url)
    host = parsed.hostname or ""

    if "render.com" in host:
        return None

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


async def wait_for_database(max_attempts: int = 40, delay_sec: float = 2.0) -> bool:
    """Try to reach the database.  Returns True if reachable, False otherwise."""
    last_err: Exception | None = None
    from app.db.session import get_resolved_url
    print(f"DEBUG: DATABASE_URL={os.environ.get('DATABASE_URL', '(unset)')}")
    print(f"DEBUG: resolved_url={get_resolved_url()}")
    eng = get_engine()
    for attempt in range(1, max_attempts + 1):
        try:
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1"))
            print(f"Database reachable (attempt {attempt}).")
            return True
        except Exception as e:
            last_err = e
            print(f"Waiting for database... ({attempt}/{max_attempts}) {e!r}")
            await asyncio.sleep(delay_sec)

    external_url = _build_external_db_url()
    if external_url is None:
        print(f"Database not reachable after {max_attempts} attempts. Service will start without DB.")
        return False

    print("Internal hostname unreachable — retrying with external .render.com hostname...")
    new_engine = create_async_engine(
        external_url,
        pool_pre_ping=True,
    )
    try:
        async with new_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        print(f"External fallback also failed: {e}. Service will start without DB.")
        return False

    print("Database reachable via external hostname.")
    from app.db.session import reinit_engine
    reinit_engine(external_url)

    sync_url = external_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    os.environ["DATABASE_URL"] = external_url
    os.environ["SYNC_DATABASE_URL"] = sync_url
    from app.core.config import get_settings
    get_settings.cache_clear()
    return True


async def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config
    
    # Run alembic upgrade head
    alembic_cfg = Config("alembic.ini")
    # Set the DB URL from settings to ensure it matches environment
    from app.core.config import get_settings
    settings = get_settings()
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    
    print("Running migrations...")
    # alembic.command is synchronous, but we are in an async loop. 
    # For a simple bootstrap script, we can run it in a thread or just run it synchronously.
    # Since this is a one-time startup script, simple synchronous call is fine or await to_thread.
    import asyncio
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    print("Migrations complete.")


async def create_schema() -> None:
    import app.models  # noqa: F401 — register mappers
    eng = get_engine()

    async with eng.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        except Exception:
            logger.warning("PostGIS extension not available — spatial queries will use Python fallback")
        await conn.run_sync(Base.metadata.create_all)


async def seed() -> None:
    import app.models  # noqa: F401

async def get_or_create_user(session, email, password, role, is_superuser=False):
    from sqlalchemy.exc import IntegrityError
    r = await session.execute(select(User).where(User.email == email))
    user = r.scalar_one_or_none()
    if not user:
        try:
            async with session.begin_nested():
                user = User(
                    email=email,
                    password_hash=hash_password(password),
                    role=role,
                    is_superuser=is_superuser,
                )
                session.add(user)
        except IntegrityError:
            r = await session.execute(select(User).where(User.email == email))
            user = r.scalar_one_or_none()
    return user

async def seed() -> None:
    import app.models  # noqa: F401
    SessionLocal = get_session_local()

    async with SessionLocal() as session:
        # 1. Admin (Multiple variations to handle frontend validation and typos)
        await get_or_create_user(session, "admin21907.com", "clutchD123", "admin", True)
        await get_or_create_user(session, "admin@21907.com", "clutchD123", "admin", True)
        await get_or_create_user(session, "admin@1907.com", "clutchD123", "admin", True)

        # 2. Vijay Kumar
        mu1 = await get_or_create_user(session, "mechanic@demo.com", "demo123456", "mechanic")
        res = await session.execute(select(Mechanic).where(Mechanic.user_id == mu1.id))
        if not res.scalar_one_or_none():
            mech = Mechanic(
                user_id=mu1.id,
                full_name="Vijay Kumar",
                phone="9876543210",
                experience="5",
                expertise=["engine", "brakes", "electrical"],
                location_address="RS Puram, Coimbatore",
                lat=11.0168,
                lon=76.9558,
                rating=4.7,
                verified=True,
                available=True,
            )
            session.add(mech)

        # 3. SpeedFix Garage
        gu1 = await get_or_create_user(session, "garage@demo.com", "demo123456", "garage")
        res = await session.execute(select(Garage).where(Garage.user_id == gu1.id))
        if not res.scalar_one_or_none():
            garage = Garage(
                user_id=gu1.id,
                garage_name="SpeedFix Auto Garage",
                owner_name="Suresh Patel",
                phone="9876543211",
                services=["engine", "brakes", "ac", "electrical", "tires"],
                mechanic_count=8,
                operating_hours="8:00 AM - 9:00 PM",
                location_address="Saibaba Colony, Coimbatore",
                lat=11.025,
                lon=76.94,
                rating=4.5,
                verified=True,
            )
            session.add(garage)

        # ---- Customer ----
        await get_or_create_user(session, "customer@demo.com", "demo123456", "customer")

        await session.commit()
        print("Seeded all demo accounts:")
        print("  admin@21907.com / clutchD123")
        print("  mechanic@demo.com / demo123456")
        print("  garage@demo.com / demo123456")
        print("  customer@demo.com / demo123456")


async def main() -> None:
    db_ok = await wait_for_database()
    if db_ok:
        await create_schema()
        await run_migrations()
        await seed()
    else:
        print("WARNING: Database unavailable. Service will start without DB tables and seed data.")


if __name__ == "__main__":
    asyncio.run(main())
