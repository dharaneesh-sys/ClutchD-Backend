"""Contract tests for ``app.services.matching._postgis_fetch``.

``_postgis_fetch`` must let callers distinguish "PostGIS is broken"
(returns ``None`` → caller falls back to the Python haversine path)
from "no providers matched the radius" (returns ``[]`` → caller must
NOT run the fallback query).
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services import matching
from app.services.matching import _postgis_fetch, nearest_garages, nearest_mechanics

_SQL = "SELECT 1"
_PARAMS = {"ulat": 28.6, "ulon": 77.2, "limit": 5, "radius_m": 10000}


def _db_raise(exc: Exception) -> AsyncMock:
    db = AsyncMock()
    db.execute.side_effect = exc
    return db


def _db_returning_rows(rows: list[dict]) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    db.execute.return_value = result
    return db


def _mechanic_row() -> dict:
    return {
        "id": uuid4(),
        "full_name": "Test Mechanic",
        "lat": 28.61,
        "lon": 77.21,
        "rating": 4.5,
        "dist_m": 1200.0,
        "expertise": ["engine"],
    }


def _garage_row() -> dict:
    return {
        "id": uuid4(),
        "garage_name": "Test Garage",
        "lat": 28.70,
        "lon": 77.10,
        "rating": 4.2,
        "dist_m": 3400.0,
        "services": ["towing"],
    }


async def test_postgis_fetch_returns_none_on_exception(caplog: pytest.LogCaptureFixture) -> None:
    """DB failure → None (not []), with a warning carrying exception context."""
    db = _db_raise(RuntimeError("PostGIS broke"))
    with caplog.at_level("WARNING", logger="app.services.matching"):
        rows = await _postgis_fetch(db, _SQL, _PARAMS)

    assert rows is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected a warning log on PostGIS failure"
    assert any(r.exc_info for r in warnings), "warning must include exc_info context"


async def test_postgis_fetch_returns_empty_list_on_legitimate_empty_result() -> None:
    """Empty query result → [] (not None): 'nothing nearby' is not a failure."""
    db = _db_returning_rows([])
    rows = await _postgis_fetch(db, _SQL, _PARAMS)

    assert rows == []


async def test_nearest_mechanics_empty_result_skips_fallback() -> None:
    """No providers within radius → [] and the fallback query never runs."""
    db = _db_returning_rows([])
    with patch.object(matching, "_fallback_mechanics", new_callable=AsyncMock) as fb:
        ranked = await nearest_mechanics(db, lat=28.6, lon=77.2)

    assert ranked == []
    fb.assert_not_awaited()


async def test_nearest_garages_empty_result_skips_fallback() -> None:
    """Same contract for garages: empty PostGIS result must not fall back."""
    db = _db_returning_rows([])
    with patch.object(matching, "_fallback_garages", new_callable=AsyncMock) as fb:
        ranked = await nearest_garages(db, lat=28.6, lon=77.2)

    assert ranked == []
    fb.assert_not_awaited()


async def test_nearest_mechanics_postgis_failure_runs_fallback_exactly_once() -> None:
    """PostGIS failure → fallback path executes exactly once (no double query)."""
    db = _db_raise(RuntimeError("PostGIS broke"))
    sentinel = [matching.RankedMechanic(
        id=uuid4(), full_name="FB", lat=0.0, lon=0.0,
        rating=3.0, distance_m=0.0, score=0.0, expertise=[],
    )]
    with patch.object(matching, "_fallback_mechanics", new_callable=AsyncMock) as fb:
        fb.return_value = sentinel
        ranked = await nearest_mechanics(db, lat=28.6, lon=77.2)

    assert ranked is sentinel
    assert fb.await_count == 1


async def test_nearest_mechanics_rows_present_returns_ranked_without_fallback() -> None:
    """Rows present → ranked mechanics built from them; fallback untouched."""
    db = _db_returning_rows([_mechanic_row()])
    with patch.object(matching, "_fallback_mechanics", new_callable=AsyncMock) as fb:
        ranked = await nearest_mechanics(db, lat=28.6, lon=77.2)

    assert len(ranked) == 1
    assert isinstance(ranked[0], matching.RankedMechanic)
    assert ranked[0].full_name == "Test Mechanic"
    fb.assert_not_awaited()
