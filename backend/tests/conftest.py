"""Test fixtures for ClutchD-Backend.

Adds ``backend/`` to ``sys.path`` so that ``app.*`` imports resolve.
Patches PostgreSQL-specific column types (ARRAY, JSONB) with SQLite-compatible
JSON variants so that ``aiosqlite`` can create tables and round-trip data.
"""

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Set env vars BEFORE any app imports so config picks them up.
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-secret-key-that-is-long-enough-for-pytest-32-bytes-min",
)
os.environ.setdefault("REDIS_URL", "memory://")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

# ── Path setup: make backend/ importable ─────────────────────────────
_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

# ── PostgreSQL → SQLite type compilers (DDL safety) ──────────────────
# These ensure CREATE TABLE emits valid SQLite DDL for PG-specific types.


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return compiler.process(Text(), **kw)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return compiler.process(Text(), **kw)


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return compiler.process(String(36), **kw)


# ── SQLite data-round-trip patches ────────────────────────────────────
# ``with_variant`` tells SQLAlchemy to use JSON storage for the sqlite
# dialect so that Python lists / dicts are correctly serialised and
# deserialised via the JSON encoder/decoder.

_JSON_VARIANT_COLUMNS: list[tuple[Any, Any]] = []


def _patch_pg_types() -> None:
    """Apply ``with_variant(JSON(), 'sqlite')`` to ARRAY & JSONB columns.

    Must be called *after* all model modules are imported so that
    ``Base.registry.mappers`` is fully populated.
    """
    from app.db.base import Base

    for mapper in Base.registry.mappers:
        for col in mapper.columns:
            orig = col.type
            if isinstance(orig, ARRAY | JSONB):
                col.type = orig.with_variant(JSON(), "sqlite")
                _JSON_VARIANT_COLUMNS.append((col, orig))


# Trigger patching at import time by pulling in all models.
# The models module re-exports every ORM model class.
from app.db.base import Base  # noqa: E402, F811
import app.models  # noqa: E402, F402  — ensure all models are registered
_patch_pg_types()

# Lazy imports needed for fixtures – imported here so conftest loading
# validates they're resolvable.
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def event_loop():
    """Create a fresh event loop shared across the test session.

    Required by ``pytest-asyncio`` when using async fixtures.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an in-memory SQLite database for testing.

    - Creates all tables from ``Base.metadata`` before each test.
    - Yields a clean ``AsyncSession``.
    - Drops all tables and disposes the engine after the test.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the test database.

    Overrides the FastAPI ``get_db`` dependency so that all API handlers
    use the same in-memory SQLite session instead of the production DB.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ── Factory fixtures ──────────────────────────────────────────────────


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> "User":
    """Create and persist a default ``User`` with role ``customer``."""
    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email="testuser@example.com",
        password_hash="$2b$12$abcdefghijklmnopqrstuvwx1234567890abcdefghijklmnopqrs",
        role="customer",
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_mechanic(db_session: AsyncSession) -> "Mechanic":
    """Create and persist a ``Mechanic`` with its own ``User``.

    The ``user`` relationship is eagerly loaded (``selectinload``) to
    prevent ``MissingGreenlet`` errors when tests access it outside the
    async SQLAlchemy greenlet context.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.mechanic import Mechanic
    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email="mechanic@example.com",
        password_hash="$2b$12$abcdefghijklmnopqrstuvwx1234567890abcdefghijklmnopqrs",
        role="mechanic",
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()

    mechanic = Mechanic(
        id=uuid.uuid4(),
        user_id=user.id,
        full_name="Test Mechanic",
        phone="+911234567890",
        experience="5 years",
        expertise=["engine", "brakes"],
        location_address="123 Test St",
        lat=28.6139,
        lon=77.2090,
        rating=4.5,
        verified=True,
        available=True,
        penalized=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(mechanic)
    await db_session.commit()

    stmt = (
        select(Mechanic)
        .where(Mechanic.id == mechanic.id)
        .options(selectinload(Mechanic.user))
    )
    result = await db_session.execute(stmt)
    return result.scalar_one()


@pytest_asyncio.fixture
async def test_garage(db_session: AsyncSession) -> "Garage":
    """Create and persist a ``Garage`` with its own ``User``.

    The ``user`` relationship is eagerly loaded (``selectinload``) to
    prevent ``MissingGreenlet`` errors when tests access it outside the
    async SQLAlchemy greenlet context.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.garage import Garage
    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email="garage@example.com",
        password_hash="$2b$12$abcdefghijklmnopqrstuvwx1234567890abcdefghijklmnopqrs",
        role="garage",
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()

    garage = Garage(
        id=uuid.uuid4(),
        user_id=user.id,
        garage_name="Test Garage",
        owner_name="Test Owner",
        phone="+911234567891",
        services=["general repair", "towing"],
        mechanic_count=3,
        operating_hours="9 AM - 6 PM",
        location_address="456 Garage Ave",
        lat=28.7041,
        lon=77.1025,
        rating=4.2,
        verified=True,
        penalized=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(garage)
    await db_session.commit()

    stmt = (
        select(Garage)
        .where(Garage.id == garage.id)
        .options(selectinload(Garage.user))
    )
    result = await db_session.execute(stmt)
    return result.scalar_one()


@pytest_asyncio.fixture
async def test_job(
    db_session: AsyncSession,
    test_user: "User",
) -> "Job":
    """Create and persist a ``Job`` owned by *test_user*."""
    from app.models.job import Job

    job = Job(
        id=uuid.uuid4(),
        user_id=test_user.id,
        issue_tag="flat_tire",
        description="Rear left tire is flat",
        request_type="auto",
        status="searching",
        customer_lat=28.6139,
        customer_lon=77.2090,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job
