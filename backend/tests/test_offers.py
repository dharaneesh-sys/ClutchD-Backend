"""Tests for offer_service.create_provider_offers().

The production code uses PostgreSQL-specific ``PGInsert`` (``ON CONFLICT``
with named constraints).  SQLite supports ``ON CONFLICT DO NOTHING`` but
not ``ON CONFLICT ON CONSTRAINT <name>``, so we monkeypatch the module-level
``PGInsert`` reference to the standard ``sqlalchemy.insert`` function.

Additionally we clear ``test_job.issue_tag`` to avoid the PostgreSQL-specific
``&&`` array-overlap operator in the fallback matching path.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

# ── PGInsert → SQLite-compatible Insert ───────────────────────────────
# SQLite's ``Insert.on_conflict_do_nothing`` doesn't accept ``constraint``;
# PostgreSQL does.  We strip the unsupported keyword so the SQLite backend
# generates ``ON CONFLICT DO NOTHING`` (without a specific constraint name).
from sqlalchemy.dialects.sqlite import Insert as _SQLiteInsert


class _CompatInsert(_SQLiteInsert):
    """SQLite Insert that silently ignores ``constraint`` keyword."""

    def on_conflict_do_nothing(self, constraint=None, index_elements=None, index_where=None):
        return super().on_conflict_do_nothing(
            index_elements=index_elements, index_where=index_where,
        )


import app.services.offer_service as _offer_svc
_offer_svc.PGInsert = _CompatInsert

# ── Patch matching to return UUID objects (SQLite raw SQL returns strings) ─
# The ``_fallback_*`` functions read ``id`` from raw SQL result mappings.  On
# SQLite this is a plain hex string; on PostgreSQL it is a native UUID.  The
# ``ProviderOffer.provider_id`` column expects a ``uuid.UUID`` for binding.
import uuid as _uuid_mod
from app.services import matching as _matching_mod

_orig_fb_mechs = _matching_mod._fallback_mechanics


async def _patched_fb_mechs(db, lat, lon, limit, issue_tag):
    results = await _orig_fb_mechs(db, lat, lon, limit, issue_tag)
    for r in results:
        r.id = _uuid_mod.UUID(str(r.id))
    return results


_orig_fb_gars = _matching_mod._fallback_garages


async def _patched_fb_gars(db, lat, lon, limit, issue_tag):
    results = await _orig_fb_gars(db, lat, lon, limit, issue_tag)
    for r in results:
        r.id = _uuid_mod.UUID(str(r.id))
    return results


_matching_mod._fallback_mechanics = _patched_fb_mechs
_matching_mod._fallback_garages = _patched_fb_gars

from app.models.provider_offer import ProviderOffer
from app.services.offer_service import create_provider_offers

# ── Patch Celery dispatch (no Redis backend in test env) ──────────────
# ``create_provider_offers`` imports ``dispatch_offer_push`` via a local
# ``from app.tasks.worker import dispatch_offer_push`` inside the function
# body, so we must patch at the worker module (not the service module).
from unittest.mock import MagicMock as _MagicMock
import app.tasks.worker as _worker_mod
_worker_mod.dispatch_offer_push = _MagicMock()

pytestmark = pytest.mark.asyncio


async def _prepare_job(db_session, test_job) -> None:
    """Set issue_tag to empty so the fallback matching avoids ``&&``."""
    test_job.issue_tag = ""
    await db_session.flush()
    await db_session.refresh(test_job)


# ── Tests ──────────────────────────────────────────────────────────────


async def test_create_offers_for_eligible_mechanics(
    db_session, test_mechanic, test_job,
):
    """T8: create_provider_offers creates ProviderOffer records for eligible mechanics.

    The default ``test_job`` has ``request_type="auto"``, so both
    mechanics and garages are considered.  At minimum the ``test_mechanic``
    (same location as the job) receives an offer.
    """
    await _prepare_job(db_session, test_job)
    count = await create_provider_offers(db_session, test_job)
    assert count > 0, "Expected at least 1 offer to be created"

    # Verify ProviderOffer rows exist in the database
    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    result = await db_session.execute(stmt)
    offers = result.scalars().all()
    assert len(offers) >= 1
    assert any(o.provider_type == "mechanic" for o in offers)
    assert any(o.provider_id == test_mechanic.id for o in offers)


async def test_no_duplicate_offers_on_retry(
    db_session, test_mechanic, test_job,
):
    """T9: calling create_provider_offers twice does not create duplicates.

    The ``ON CONFLICT DO NOTHING`` clause should suppress re-insertion
    of offers that already exist for the same (job, provider_type, provider_id).
    """
    await _prepare_job(db_session, test_job)

    # First call — should create offers
    count1 = await create_provider_offers(db_session, test_job)
    assert count1 > 0, "First call should create offers"

    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    result = await db_session.execute(stmt)
    offers_after_first = result.scalars().all()
    first_count = len(offers_after_first)

    # Second call — should create 0 new offers (duplicates suppressed)
    count2 = await create_provider_offers(db_session, test_job)
    assert count2 == 0, "Second call should create 0 new offers (duplicates suppressed)"

    result = await db_session.execute(stmt)
    offers_after_second = result.scalars().all()
    assert len(offers_after_second) == first_count


async def test_no_offers_for_assigned_job(
    db_session, test_mechanic, test_job,
):
    """T10: a job with status != 'searching' receives no offers.

    ``create_provider_offers`` acquires a ``FOR UPDATE`` lock on jobs
    whose status is ``searching``.  If the job is already assigned
    the lock is never acquired and the function returns 0.
    """
    await _prepare_job(db_session, test_job)
    test_job.status = "assigned"
    await db_session.flush()

    count = await create_provider_offers(db_session, test_job)
    assert count == 0


async def test_expires_at_set_correctly(
    db_session, test_mechanic, test_job,
):
    """T11: expires_at is set to approximately now + 15 minutes."""
    await _prepare_job(db_session, test_job)

    before = datetime.now(timezone.utc)
    count = await create_provider_offers(db_session, test_job)
    after = datetime.now(timezone.utc)
    assert count > 0

    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    result = await db_session.execute(stmt)
    offers = result.scalars().all()

    for off in offers:
        exp = off.expires_at
        # SQLite returns naive datetimes even for DateTime(timezone=True);
        # make it aware for comparison.
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        expected_min = before + timedelta(minutes=15)
        expected_max = after + timedelta(minutes=15)
        assert expected_min - timedelta(seconds=1) <= exp <= expected_max + timedelta(seconds=1), (
            f"expires_at {exp} not within expected range "
            f"[{expected_min}, {expected_max}]"
        )


async def test_mechanic_request_type_gets_only_mechanics(
    db_session, test_mechanic, test_garage, test_job,
):
    """T12: request_type='mechanic' creates offers only for mechanics.

    Even when a garage is eligible, it should not receive an offer when
    the job's request_type restricts to mechanics.
    """
    await _prepare_job(db_session, test_job)
    test_job.request_type = "mechanic"
    await db_session.flush()

    count = await create_provider_offers(db_session, test_job)
    assert count > 0

    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    result = await db_session.execute(stmt)
    offers = result.scalars().all()

    assert all(o.provider_type == "mechanic" for o in offers), (
        "All offers should be of type 'mechanic'"
    )
    assert any(o.provider_id == test_mechanic.id for o in offers)


async def test_garage_request_type_gets_only_garages(
    db_session, test_mechanic, test_garage, test_job,
):
    """T13: request_type='garage' creates offers only for garages."""
    await _prepare_job(db_session, test_job)
    test_job.request_type = "garage"
    await db_session.flush()

    count = await create_provider_offers(db_session, test_job)
    assert count > 0

    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    result = await db_session.execute(stmt)
    offers = result.scalars().all()

    assert all(o.provider_type == "garage" for o in offers), (
        "All offers should be of type 'garage'"
    )
    assert any(o.provider_id == test_garage.id for o in offers)


async def test_auto_request_type_gets_both(
    db_session, test_mechanic, test_garage, test_job,
):
    """T14: request_type='auto' creates offers for mechanics AND garages."""
    await _prepare_job(db_session, test_job)
    test_job.request_type = "auto"
    await db_session.flush()

    count = await create_provider_offers(db_session, test_job)
    assert count > 0

    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    result = await db_session.execute(stmt)
    offers = result.scalars().all()

    types = {o.provider_type for o in offers}
    assert "mechanic" in types, "Expected at least one mechanic offer"
    assert "garage" in types, "Expected at least one garage offer"


async def test_offer_status_is_pending(
    db_session, test_mechanic, test_job,
):
    """T?: newly created offers have status='pending'."""
    await _prepare_job(db_session, test_job)
    count = await create_provider_offers(db_session, test_job)
    assert count > 0

    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    result = await db_session.execute(stmt)
    offers = result.scalars().all()

    assert all(o.status == "pending" for o in offers)
