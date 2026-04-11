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


async def create_schema() -> None:
    import app.models  # noqa: F401 — register mappers

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)


async def seed() -> None:
    import app.models  # noqa: F401

    async with AsyncSessionLocal() as session:
        r = await session.execute(select(User).where(User.email == "admin@mechoncall.com"))
        if r.scalar_one_or_none():
            await session.commit()
            print("Seed skipped (admin already exists).")
            return

        admin = User(
            email="admin@mechoncall.com",
            password_hash=hash_password("AdminChangeMe!"),
            role="admin",
            is_superuser=True,
        )
        session.add(admin)

        demo_mech_user = User(
            email="mechanic@demo.com",
            password_hash=hash_password("demo123456"),
            role="mechanic",
        )
        session.add(demo_mech_user)
        await session.flush()

        mech = Mechanic(
            user_id=demo_mech_user.id,
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

        gu = User(
            email="garage@demo.com",
            password_hash=hash_password("demo123456"),
            role="garage",
        )
        session.add(gu)
        await session.flush()

        garage = Garage(
            user_id=gu.id,
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

        cu = User(
            email="customer@demo.com",
            password_hash=hash_password("demo123456"),
            role="customer",
        )
        session.add(cu)

        await session.commit()
        print("Seeded admin@mechoncall.com / AdminChangeMe!, mechanic@demo.com, garage@demo.com, customer@demo.com (password demo123456)")


async def main() -> None:
    await wait_for_database()
    await create_schema()
    await seed()


if __name__ == "__main__":
    asyncio.run(main())
