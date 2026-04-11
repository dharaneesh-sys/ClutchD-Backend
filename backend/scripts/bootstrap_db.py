#!/usr/bin/env python3
"""Create PostGIS extension, SQLAlchemy tables, and seed demo data."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, text

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User


async def wait_for_database(max_attempts: int = 40, delay_sec: float = 2.0) -> None:
    """PostGIS image restarts Postgres after init; healthcheck can pass in a gap — retry until stable."""
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            print(f"Database reachable (attempt {attempt}).")
            return
        except Exception as e:
            last_err = e
            print(f"Waiting for database... ({attempt}/{max_attempts}) {e!r}")
            await asyncio.sleep(delay_sec)
    raise RuntimeError(f"Database not reachable after {max_attempts} attempts") from last_err


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

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        # We still run create_all for regular tables that might not be in alembic yet
        # though ideally everything should be in alembic.
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

    async with AsyncSessionLocal() as session:
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

        # ---- Extra Mechanic 1: Arjun Reddy ----
        mu2 = await get_or_create_user(session, "arjun@demo.com", "demo123456", "mechanic")
        res = await session.execute(select(Mechanic).where(Mechanic.user_id == mu2.id))
        if not res.scalar_one_or_none():
            mech2 = Mechanic(
                user_id=mu2.id,
                full_name="Arjun Reddy",
                phone="9871234567",
                experience="8",
                expertise=["engine", "transmission", "diagnostics", "oil"],
                location_address="Gandhipuram, Coimbatore",
                lat=11.0185,
                lon=76.9725,
                rating=4.9,
                verified=True,
                available=True,
            )
            session.add(mech2)

        # ---- Extra Mechanic 2: Deepa Nair ----
        mu3 = await get_or_create_user(session, "deepa@demo.com", "demo123456", "mechanic")
        res = await session.execute(select(Mechanic).where(Mechanic.user_id == mu3.id))
        if not res.scalar_one_or_none():
            mech3 = Mechanic(
                user_id=mu3.id,
                full_name="Deepa Nair",
                phone="9998877665",
                experience="3",
                expertise=["electrical", "battery", "ac", "diagnostics"],
                location_address="Peelamedu, Coimbatore",
                lat=11.0310,
                lon=76.9880,
                rating=4.6,
                verified=True,
                available=True,
            )
            session.add(mech3)

        # ---- Extra Garage: AutoCare Hub ----
        gu2 = await get_or_create_user(session, "autocare@demo.com", "demo123456", "garage")
        res = await session.execute(select(Garage).where(Garage.user_id == gu2.id))
        if not res.scalar_one_or_none():
            garage2 = Garage(
                user_id=gu2.id,
                garage_name="AutoCare Hub",
                owner_name="Kavitha Rajan",
                phone="9876501234",
                services=["engine", "transmission", "brakes", "tires", "suspension", "diagnostics"],
                mechanic_count=12,
                operating_hours="7:00 AM - 10:00 PM",
                location_address="Avinashi Road, Coimbatore",
                lat=11.0245,
                lon=76.9810,
                rating=4.8,
                verified=True,
            )
            session.add(garage2)

        # ---- Customer ----
        await get_or_create_user(session, "customer@demo.com", "demo123456", "customer")

        await session.commit()
        print("Seeded all demo accounts (password: demo123456):")
        print("  admin21907.com / clutchD123")
        print("  mechanic@demo.com, arjun@demo.com, deepa@demo.com")
        print("  garage@demo.com, autocare@demo.com")
        print("  customer@demo.com")


async def main() -> None:
    await wait_for_database()
    await create_schema()
    await run_migrations()
    await seed()


if __name__ == "__main__":
    asyncio.run(main())
