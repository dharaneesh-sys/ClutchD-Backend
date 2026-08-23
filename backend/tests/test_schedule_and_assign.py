"""Tests for Task 4 — schedule payload gate + assigned_type column fix.

Covers two bugs in ``app/services/offer_service.py``:

1. ``accept_offer`` wrote the phantom attribute ``assigned_provider_type``
   while the real column (``models/job.py``) is ``assigned_type`` — the
   assignment was silently dropped on flush, leaving the DB column NULL.
2. ``create_provider_offers`` dispatched offers immediately even for jobs
   with a future ``scheduled_at`` — scheduled bookings must NOT create
   ProviderOffer rows until they are due.

Mock/compat style mirrors ``tests/test_offers.py`` and
``tests/test_accept_decline_offers.py``.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

# ── PGInsert → SQLite-compatible Insert ───────────────────────────────
# SQLite cannot handle ``ON CONFLICT ON CONSTRAINT <name>``; strip it.
from sqlalchemy.dialects.sqlite import Insert as _SQLiteInsert


class _CompatInsert(_SQLiteInsert):
    """SQLite Insert that silently ignores ``constraint`` keyword."""

    def on_conflict_do_nothing(self, constraint=None, index_elements=None, index_where=None):
        return super().on_conflict_do_nothing(
            index_elements=index_elements, index_where=index_where,
        )


import app.services.offer_service as _offer_svc
_offer_svc.PGInsert = _CompatInsert

# ── Patch matching fallback to return UUID objects (SQLite raw SQL) ────
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

# ── Patch Celery dispatch (no Redis backend in test env) ──────────────
from unittest.mock import MagicMock as _MagicMock

import app.tasks.worker as _worker_mod
_worker_mod.dispatch_offer_push = _MagicMock()

# ── Rate-limiter patch (offers API routes use slowapi) ─────────────────
from limits.storage import MemoryStorage  # noqa: E402

import app.core.limiter as _limiter_mod  # noqa: E402
_limiter_mod.limiter.limiter.storage = MemoryStorage()

import app.api.v1.offers as _offers_mod  # noqa: E402
_offers_mod.limiter.limiter.storage = MemoryStorage()

from app.models.provider_offer import ProviderOffer  # noqa: E402
from app.services.offer_service import (  # noqa: E402
    accept_offer,
    create_provider_offers,
)

pytestmark = pytest.mark.asyncio


def _auth_header(user_id: str) -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token(subject=user_id)
    return {"Authorization": f"Bearer {token}"}


# ── assigned_type persistence ──────────────────────────────────────────


async def test_accept_offer_persists_assigned_type(
    client, db_session, test_mechanic, test_job,
):
    """accept_offer must persist the REAL column ``assigned_type``.

    Regression guard for the ``assigned_provider_type`` typo: the ORM
    silently accepted the phantom attribute and dropped the value on
    flush, leaving ``jobs.assigned_type`` NULL forever.
    """
    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job.id,
        provider_type="mechanic",
        provider_id=test_mechanic.id,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)
    await db_session.flush()
    assert test_job.assigned_type is None  # NULL before accept

    headers = _auth_header(str(test_mechanic.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/accept", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["job_status"] == "assigned"

    await db_session.refresh(test_job)
    assert test_job.status == "assigned"
    assert test_job.assigned_mechanic_id == test_mechanic.id
    # The actual DB column must now hold the provider type.
    assert test_job.assigned_type == "mechanic"
    # The typo'd attribute must no longer be set anywhere.
    assert getattr(test_job, "assigned_provider_type", None) is None


async def test_accept_offer_service_layer_persists_assigned_type(
    db_session, test_mechanic, test_job,
):
    """Service-layer variant: accept_offer writes assigned_type via flush."""
    from app.models.user import User

    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job.id,
        provider_type="mechanic",
        provider_id=test_mechanic.id,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)
    await db_session.flush()

    stmt = select(User).where(User.id == test_mechanic.user_id)
    user = (await db_session.execute(stmt)).scalar_one()

    result = await accept_offer(db_session, user, offer.id)
    assert result["job_status"] == "assigned"
    assert test_job.assigned_type == "mechanic"


# ── Helpers ────────────────────────────────────────────────────────────


async def _prepare_job(db_session, test_job, scheduled_at):
    """Set schedule + clear issue_tag so SQLite fallback matches unfiltered.

    Mirrors ``tests/test_offers.py::_prepare_job``: an empty issue_tag
    avoids the PostgreSQL ``&&`` array-overlap operator on SQLite and
    keeps the expertise filter from excluding the seeded mechanic.
    """
    test_job.issue_tag = ""
    test_job.scheduled_at = scheduled_at
    await db_session.flush()
    await db_session.refresh(test_job)


# ── scheduled_at gate ──────────────────────────────────────────────
async def test_future_scheduled_job_creates_no_offers(
    db_session, test_mechanic, test_job,
):
    """A job scheduled in the future must create ZERO ProviderOffer rows."""
    await _prepare_job(
        db_session, test_job, datetime.now(timezone.utc) + timedelta(hours=1),
    )

    count = await create_provider_offers(db_session, test_job)
    assert count == 0, "Future-scheduled job must not be dispatched immediately"

    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 0
    # Job stays in searching state awaiting its slot.
    await db_session.refresh(test_job)
    assert test_job.status == "searching"


async def test_due_scheduled_job_creates_offers(
    db_session, test_mechanic, test_job,
):
    """Once the schedule is due (past), dispatch works normally."""
    await _prepare_job(
        db_session, test_job, datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    count = await create_provider_offers(db_session, test_job)
    assert count > 0, "Due/past scheduled job should create offers"

    stmt = select(ProviderOffer).where(ProviderOffer.job_id == test_job.id)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) >= 1


async def test_immediate_job_still_dispatches(
    db_session, test_mechanic, test_job,
):
    """scheduled_at=None (on-demand) keeps the existing behavior."""
    await _prepare_job(db_session, test_job, None)

    count = await create_provider_offers(db_session, test_job)
    assert count > 0
