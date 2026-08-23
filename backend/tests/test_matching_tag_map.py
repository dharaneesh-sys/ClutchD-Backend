"""Tests for ``ISSUE_TO_EXPERTISE`` mapping and deterministic fallback.

Mirrors ClutchD-App/src/lib/constants.js exactly. Verifies:
- flat_tire → tires resolves and finds a tires expert
- other / unknown tags fall back to unfiltered dispatch
- fallback SQL is deterministic (ORDER BY)
- PostGIS path uses mapped expertise, not raw tag
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User
from app.services import matching
from app.services.matching import (
    ISSUE_TO_EXPERTISE,
    _fallback_garages,
    _fallback_mechanics,
    issue_tag_to_expertise,
    nearest_garages,
    nearest_mechanics,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Mapping correctness — must mirror frontend constants.js exactly
# ---------------------------------------------------------------------------

EXPECTED_MAP = {
    "flat_tire": "tires",
    "engine_failure": "engine",
    "battery_dead": "battery",
    "overheating": "engine",
    "brake_issue": "brakes",
    "oil_leak": "oil",
    "electrical": "electrical",
    "ac_not_working": "ac",
    "transmission": "transmission",
    "starting_issue": "engine",
    "noise": "diagnostics",
    "other": None,
}


def test_issue_to_expertise_dict_exact():
    assert ISSUE_TO_EXPERTISE == EXPECTED_MAP


def test_issue_to_expertise_has_12_keys():
    assert len(ISSUE_TO_EXPERTISE) == 12


def test_issue_tag_to_expertise_all_known():
    for tag, exp in EXPECTED_MAP.items():
        assert issue_tag_to_expertise(tag) == exp, f"{tag} -> {exp}"


def test_issue_tag_to_expertise_unknown_returns_none():
    assert issue_tag_to_expertise("bogus_unknown_tag") is None
    assert issue_tag_to_expertise("") is None
    assert issue_tag_to_expertise(None) is None


def test_issue_tag_to_expertise_other_is_none():
    assert issue_tag_to_expertise("other") is None


def test_resolve_is_idempotent_for_expertise_values():
    # offer_service pre-maps flat_tire→tires then matching re-resolves tires→tires
    assert matching._resolve_expertise("tires") == "tires"
    assert matching._resolve_expertise("engine") == "engine"
    assert matching._resolve_expertise("flat_tire") == "tires"
    assert matching._resolve_expertise("other") is None
    assert matching._resolve_expertise("unknown_xyz") is None


# ---------------------------------------------------------------------------
# Helpers for DB seeding
# ---------------------------------------------------------------------------

async def _seed_mechanic(db_session, *, expertise, lat=28.6139, lon=77.2090, **overrides):
    user = User(
        id=uuid.uuid4(),
        email=f"mech_{uuid.uuid4().hex[:6]}@test.com",
        password_hash="$2b$12$abcdefghijklmnopqrstuvwx1234567890abcdefghijklmnopqrs",
        role="mechanic",
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    mech = Mechanic(
        id=uuid.uuid4(),
        user_id=user.id,
        full_name="Tires Specialist",
        phone="+911234567890",
        experience="5 years",
        expertise=expertise,
        location_address="Test St",
        lat=lat,
        lon=lon,
        rating=4.5,
        verified=True,
        available=True,
        penalized=False,
        created_at=datetime.now(timezone.utc),
    )
    for k, v in overrides.items():
        setattr(mech, k, v)
    db_session.add(mech)
    await db_session.flush()
    return mech


async def _seed_garage(db_session, *, services, lat=28.6139, lon=77.2090):
    user = User(
        id=uuid.uuid4(),
        email=f"gar_{uuid.uuid4().hex[:6]}@test.com",
        password_hash="$2b$12$abcdefghijklmnopqrstuvwx1234567890abcdefghijklmnopqrs",
        role="garage",
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    gar = Garage(
        id=uuid.uuid4(),
        user_id=user.id,
        garage_name="Tires Garage",
        owner_name="Owner",
        phone="+911234567891",
        services=services,
        mechanic_count=2,
        operating_hours="9-6",
        location_address="Addr",
        lat=lat,
        lon=lon,
        rating=4.2,
        verified=True,
        penalized=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(gar)
    await db_session.flush()
    return gar


# ---------------------------------------------------------------------------
# Integration — fallback path via SQLite (PostGIS unavailable)
# ---------------------------------------------------------------------------

async def test_flat_tire_finds_tires_mechanic(db_session):
    """Seeded mechanic with expertise=['tires'] must be found via flat_tire."""
    mech = await _seed_mechanic(db_session, expertise=["tires"])
    results = await nearest_mechanics(db_session, lat=28.6139, lon=77.2090, limit=10, issue_tag="flat_tire")
    assert len(results) >= 1
    ids = [str(r.id) for r in results]
    assert str(mech.id) in ids
    # verify the mapping was applied — the mechanic's expertise should contain the mapped value
    assert any("tires" in r.expertise for r in results)


async def test_flat_tire_finds_tires_garage(db_session):
    """Garage services=['tires'] found via flat_tire (same mapping, services column)."""
    gar = await _seed_garage(db_session, services=["tires"])
    results = await nearest_garages(db_session, lat=28.6139, lon=77.2090, limit=10, issue_tag="flat_tire")
    assert len(results) >= 1
    assert str(gar.id) in [str(r.id) for r in results]


async def test_other_returns_unfiltered(db_session):
    """issue_tag='other' and unknown tags must return unfiltered list."""
    # Seed one tires expert far from query? No — seed at query location so distance passes
    tires_mech = await _seed_mechanic(db_session, expertise=["tires"])
    # 'other' → None → unfiltered, should return the seeded mechanic
    results_other = await nearest_mechanics(db_session, lat=28.6139, lon=77.2090, limit=10, issue_tag="other")
    assert len(results_other) >= 1
    assert str(tires_mech.id) in [str(r.id) for r in results_other]

    # unknown tag also unfiltered
    results_unknown = await nearest_mechanics(db_session, lat=28.6139, lon=77.2090, limit=10, issue_tag="does_not_exist")
    assert len(results_unknown) >= 1
    assert str(tires_mech.id) in [str(r.id) for r in results_unknown]

    # garages same contract
    gar = await _seed_garage(db_session, services=["engine"])
    results_gar_other = await nearest_garages(db_session, lat=28.6139, lon=77.2090, limit=10, issue_tag="other")
    assert str(gar.id) in [str(r.id) for r in results_gar_other]
    results_gar_unknown = await nearest_garages(db_session, lat=28.6139, lon=77.2090, limit=10, issue_tag="bogus")
    assert str(gar.id) in [str(r.id) for r in results_gar_unknown]


async def test_electrical_maps_to_itself(db_session):
    """electrical is one of the 2/12 direct overlaps — should still filter correctly."""
    mech = await _seed_mechanic(db_session, expertise=["electrical"])
    results = await nearest_mechanics(db_session, lat=28.6139, lon=77.2090, limit=10, issue_tag="electrical")
    assert str(mech.id) in [str(r.id) for r in results]


async def test_transmission_maps_to_itself(db_session):
    mech = await _seed_mechanic(db_session, expertise=["transmission"])
    results = await nearest_mechanics(db_session, lat=28.6139, lon=77.2090, limit=10, issue_tag="transmission")
    assert str(mech.id) in [str(r.id) for r in results]


# ---------------------------------------------------------------------------
# PostGIS path — mock _postgis_fetch, verify mapped param is used
# ---------------------------------------------------------------------------

def _mock_db_with_postgis_rows(rows):
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    db.execute.return_value = result
    return db


async def test_nearest_mechanics_uses_mapped_tag_via_postgis():
    """flat_tire must be sent as tires to the PostGIS query, not as flat_tire."""
    captured = {}

    async def fake_postgis(db, sql, params):
        captured.update(params)
        # Simulate that PostGIS returned one row with tires expertise
        return [
            {
                "id": uuid.uuid4(),
                "full_name": "Mock",
                "lat": 28.61,
                "lon": 77.20,
                "rating": 4.5,
                "dist_m": 500.0,
                "expertise": ["tires"],
            }
        ]

    with patch.object(matching, "_postgis_fetch", side_effect=fake_postgis):
        db = AsyncMock()
        results = await nearest_mechanics(db, lat=28.6, lon=77.2, limit=10, issue_tag="flat_tire")
        assert captured.get("tag") == "tires", f"expected mapped tag 'tires', got {captured.get('tag')}"
        assert len(results) == 1

    # unknown tag must be unfiltered → no tag in params, sql_all used
    captured.clear()
    with patch.object(matching, "_postgis_fetch", side_effect=fake_postgis) as mock_pg:
        db = AsyncMock()
        # Make fake return empty for unfiltered case check: we just care that _postgis_fetch was called with sql_all (no tag)
        # We'll inspect that the sql used was sql_all by checking params has no tag
        await nearest_mechanics(db, lat=28.6, lon=77.2, limit=10, issue_tag="other")
        assert "tag" not in captured or captured.get("tag") is None


async def test_nearest_garages_uses_mapped_tag_via_postgis():
    captured = {}

    async def fake_postgis(db, sql, params):
        captured.update(params)
        return [
            {
                "id": uuid.uuid4(),
                "garage_name": "Mock Garage",
                "lat": 28.61,
                "lon": 77.20,
                "rating": 4.0,
                "dist_m": 800.0,
                "services": ["tires"],
            }
        ]

    with patch.object(matching, "_postgis_fetch", side_effect=fake_postgis):
        db = AsyncMock()
        await nearest_garages(db, lat=28.6, lon=77.2, limit=10, issue_tag="flat_tire")
        assert captured.get("tag") == "tires"


# ---------------------------------------------------------------------------
# Determinism — fallback SQL must contain ORDER BY before LIMIT
# ---------------------------------------------------------------------------

async def test_fallback_mechanics_query_is_deterministic():
    """Fallback query must be ORDER BY u.created_at, u.id before LIMIT."""
    db = AsyncMock()
    # Capture the SQL passed to db.execute inside _fallback_mechanics
    captured_sql = []

    async def capture_execute(q, params):
        captured_sql.append(str(q))
        m = MagicMock()
        m.mappings.return_value.all.return_value = []
        return m

    db.execute.side_effect = capture_execute
    # Call with flat_tire so filtered branch is exercised
    await _fallback_mechanics(db, lat=28.6, lon=77.2, limit=10, issue_tag="flat_tire")
    assert captured_sql, "expected db.execute to be called"
    sql = captured_sql[0]
    assert "ORDER BY" in sql
    assert "u.created_at" in sql
    assert "u.id" in sql
    # ORDER BY must appear before LIMIT
    assert sql.index("ORDER BY") < sql.index("LIMIT")


async def test_fallback_garages_query_is_deterministic():
    db = AsyncMock()
    captured_sql = []

    async def capture_execute(q, params):
        captured_sql.append(str(q))
        m = MagicMock()
        m.mappings.return_value.all.return_value = []
        return m

    db.execute.side_effect = capture_execute
    await _fallback_garages(db, lat=28.6, lon=77.2, limit=10, issue_tag="flat_tire")
    sql = captured_sql[0]
    assert "ORDER BY" in sql
    assert "u.created_at" in sql
    assert sql.index("ORDER BY") < sql.index("LIMIT")


async def test_fallback_mechanics_unfiltered_also_ordered():
    db = AsyncMock()
    captured_sql = []

    async def capture_execute(q, params):
        captured_sql.append(str(q))
        m = MagicMock()
        m.mappings.return_value.all.return_value = []
        return m

    db.execute.side_effect = capture_execute
    await _fallback_mechanics(db, lat=28.6, lon=77.2, limit=10, issue_tag="other")
    sql = captured_sql[0]
    assert "ORDER BY" in sql
    assert "LIMIT" in sql


async def test_source_contains_order_by_twice():
    """Guard: file must contain at least 2 ORDER BY for each fallback (filtered + unfiltered)."""
    import pathlib

    src = pathlib.Path(matching.__file__).read_text()
    # Each fallback has 2 queries (filtered + unfiltered) each with ORDER BY, so >=4 total
    assert src.count("ORDER BY u.created_at") >= 4
