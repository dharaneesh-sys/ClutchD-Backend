"""Tests for matching.py eligibility filters.

All tests use the fallback path (Python haversine) because SQLite does
not support PostGIS functions such as ST_DWithin, ST_MakePoint, etc.
The ``_postgis_fetch`` helper catches the PostGIS exception and returns
``[]``, causing ``nearest_mechanics`` / ``nearest_garages`` to delegate
to ``_fallback_mechanics`` / ``_fallback_garages`` automatically.

We pass ``issue_tag=None`` throughout to avoid the PostgreSQL-specific
``&&`` array-overlap operator used in the *issue_tag* branch of the
fallback — that operator is not available on SQLite either.
"""

import math
import uuid
from datetime import datetime, timezone

import pytest

from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User
from app.services import matching

pytestmark = pytest.mark.asyncio


# ── Helpers ─────────────────────────────────────────────────────────────

async def _create_mechanic(db_session, **overrides) -> Mechanic:
    """Persist a User + Mechanic pair and return the Mechanic.

    Accepts keyword overrides for both models (e.g. ``is_active`` on the
    User, or ``penalized`` / ``verified`` on the Mechanic).
    """
    user = User(
        id=uuid.uuid4(),
        email=f"mech_{uuid.uuid4().hex[:8]}@test.com",
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
    # Apply overrides that exist on Mechanic
    for key in ("penalized", "verified", "available", "lat", "lon", "rating"):
        if key in overrides:
            setattr(mechanic, key, overrides.pop(key))
    db_session.add(mechanic)
    await db_session.flush()

    # Apply remaining overrides (User fields)
    for key in ("is_active",):
        if key in overrides:
            setattr(user, key, overrides.pop(key))
            await db_session.flush()

    return mechanic


async def _nearest_mechs(db_session, lat=28.6139, lon=77.2090):
    """Call ``nearest_mechanics`` with ``issue_tag=None`` (fallback path)."""
    return await matching.nearest_mechanics(db_session, lat=lat, lon=lon, limit=20, issue_tag=None)


async def _nearest_gars(db_session, lat=28.6139, lon=77.2090):
    """Call ``nearest_garages`` with ``issue_tag=None`` (fallback path)."""
    return await matching.nearest_garages(db_session, lat=lat, lon=lon, limit=20, issue_tag=None)


# ── Exclusion filters (T1, T2, T6, T7) ─────────────────────────────────

async def test_penalized_mechanic_excluded(db_session):
    """T1: penalized=True mechanic is excluded from matching."""
    await _create_mechanic(db_session, penalized=True)
    results = await _nearest_mechs(db_session)
    assert len(results) == 0


async def test_inactive_user_excluded(db_session):
    """T2: user.is_active=False mechanic excluded via JOIN to users."""
    # Custom helper path: create user with is_active=False
    user = User(
        id=uuid.uuid4(),
        email=f"inactive_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="$2b$12$abcdefghijklmnopqrstuvwx1234567890abcdefghijklmnopqrs",
        role="mechanic",
        is_active=False,
        is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()

    mechanic = Mechanic(
        id=uuid.uuid4(),
        user_id=user.id,
        full_name="Inactive User Mech",
        phone="+911234567890",
        experience="3 years",
        expertise=["engine"],
        location_address="123 Test St",
        lat=28.6139,
        lon=77.2090,
        rating=4.0,
        verified=True,
        available=True,
        penalized=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(mechanic)
    await db_session.flush()

    results = await _nearest_mechs(db_session)
    assert len(results) == 0


async def test_unavailable_mechanic_excluded(db_session):
    """T6: available=False mechanic excluded."""
    await _create_mechanic(db_session, available=False)
    results = await _nearest_mechs(db_session)
    assert len(results) == 0


async def test_unverified_mechanic_excluded(db_session):
    """T7: verified=False mechanic excluded."""
    await _create_mechanic(db_session, verified=False)
    results = await _nearest_mechs(db_session)
    assert len(results) == 0


# ── Radius / inclusion tests (T4, T5, T15) ─────────────────────────────

async def test_mechanic_within_radius_included(db_session):
    """T4: mechanic at the query location IS included (distance ≈ 0)."""
    mech = await _create_mechanic(db_session, lat=28.6139, lon=77.2090)
    results = await _nearest_mechs(db_session, lat=28.6139, lon=77.2090)
    assert len(results) >= 1
    # SQLite raw SQL returns UUID as a hex string; normalize for comparison
    assert uuid.UUID(str(results[0].id)) == mech.id
    assert results[0].distance_m == 0.0


async def test_mechanic_outside_radius_excluded(db_session):
    """T5: mechanic far from query point (≈8300 km) excluded."""
    await _create_mechanic(db_session, lat=0.0, lon=0.0)
    results = await _nearest_mechs(db_session, lat=28.6139, lon=77.2090)
    assert len(results) == 0


async def test_garage_outside_radius_excluded(db_session):
    """T15: garage outside search radius excluded."""
    user = User(
        id=uuid.uuid4(),
        email=f"garage_out_{uuid.uuid4().hex[:8]}@test.com",
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
        garage_name="Far Garage",
        owner_name="Far Owner",
        phone="+911234567891",
        services=["towing"],
        mechanic_count=2,
        operating_hours="9 AM - 6 PM",
        location_address="Far Away",
        lat=0.0,
        lon=0.0,
        rating=4.0,
        verified=True,
        penalized=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(garage)
    await db_session.flush()

    results = await _nearest_gars(db_session, lat=28.6139, lon=77.2090)
    assert len(results) == 0


# ── Sorting and scoring (T16, T3) ──────────────────────────────────────

async def test_garages_sorted_by_score_descending(db_session):
    """T16: garages returned sorted by score descending."""
    # Garage 1 — closer, lower rating
    u1 = User(
        id=uuid.uuid4(), email=f"g1_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="$2b$12$x", role="garage",
        is_active=True, is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(u1)
    await db_session.flush()
    g1 = Garage(
        id=uuid.uuid4(), user_id=u1.id, garage_name="Close Garage",
        owner_name="O1", phone="+91", services=["towing"],
        mechanic_count=2, operating_hours="9-6",
        location_address="Addr", lat=28.6139, lon=77.2090,
        rating=4.0, verified=True, penalized=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(g1)
    await db_session.flush()

    # Garage 2 — slightly farther, higher rating → likely higher score
    u2 = User(
        id=uuid.uuid4(), email=f"g2_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="$2b$12$x", role="garage",
        is_active=True, is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(u2)
    await db_session.flush()
    g2 = Garage(
        id=uuid.uuid4(), user_id=u2.id, garage_name="Far Garage",
        owner_name="O2", phone="+91", services=["towing"],
        mechanic_count=2, operating_hours="9-6",
        location_address="Addr", lat=28.62, lon=77.21,
        rating=4.5, verified=True, penalized=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(g2)
    await db_session.flush()

    results = await _nearest_gars(db_session, lat=28.6139, lon=77.2090)
    assert len(results) == 2
    for i in range(len(results) - 1):
        assert results[i].score >= results[i + 1].score


async def test_score_calculation(db_session):
    """T3: _score uses rating and distance correctly.

    Formula: score = (rating or 3.0) * 2.0 - dist_km * 0.8 + avail_bonus.
    """
    # distance_m = 10 km, rating = 4.0, no bonus
    score = matching._score(distance_m=10_000, rating=4.0, availability_bonus=0.0)
    # 4.0 * 2.0 - (10_000 / 1000) * 0.8 + 0.0 = 8.0 - 8.0 + 0.0 = 0.0
    assert math.isclose(score, 0.0, rel_tol=1e-9)


async def test_score_prefers_higher_rating(db_session):
    """T? Given equal distance, higher-rated mechanic scores higher."""
    s_low = matching._score(distance_m=5_000, rating=3.0, availability_bonus=0.5)
    s_high = matching._score(distance_m=5_000, rating=5.0, availability_bonus=0.5)
    assert s_high > s_low


async def test_mechanics_sorted_by_score(db_session):
    """T? Mechanics returned sorted by score descending."""
    await _create_mechanic(db_session, lat=28.6139, lon=77.2090, rating=4.0)
    await _create_mechanic(db_session, lat=28.62, lon=77.21, rating=4.8)
    results = await _nearest_mechs(db_session)
    assert len(results) == 2
    for i in range(len(results) - 1):
        assert results[i].score >= results[i + 1].score


# ── Edge cases (T17) ───────────────────────────────────────────────────

async def test_fallback_mechanics_empty_when_no_matches(db_session):
    """T17: nearest_mechanics returns [] when no eligible mechanics exist."""
    results = await _nearest_mechs(db_session)
    assert results == []


async def test_fallback_garages_empty_when_no_matches(db_session):
    """T? nearest_garages returns [] when no eligible garages exist."""
    results = await _nearest_gars(db_session)
    assert results == []


# ── Pure function tests ────────────────────────────────────────────────

async def test_haversine_m_zero_distance(db_session):
    """T? haversine_m returns 0.0 for identical coordinates."""
    dist = matching.haversine_m(28.6139, 77.2090, 28.6139, 77.2090)
    assert dist == 0.0


async def test_haversine_m_known_distance(db_session):
    """T? haversine_m computes Delhi→Mumbai distance correctly."""
    # Delhi (28.6139, 77.2090) → Mumbai (19.0760, 72.8777) ~ 1 159 km
    dist = matching.haversine_m(28.6139, 77.2090, 19.0760, 72.8777)
    assert 1_100_000 < dist < 1_200_000
