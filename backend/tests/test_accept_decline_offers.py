"""Tests for POST /offers/{id}/accept and POST /offers/{id}/decline.

Covers authentication, authorization, ownership validation, status
conflicts, expiry, and concurrent-accept atomicity.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.job import Job
from app.models.provider_offer import ProviderOffer

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def test_mechanic2(db_session):
    """A second mechanic for concurrency tests."""
    from app.models.mechanic import Mechanic
    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email="mechanic2@example.com",
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
        full_name="Test Mechanic 2",
        phone="+911234567892",
        experience="3 years",
        expertise=["transmission"],
        location_address="456 Other St",
        lat=28.6139,
        lon=77.2090,
        rating=4.0,
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


# ── Rate-limiter patch ──────────────────────────────────────────────────
from limits.storage import MemoryStorage  # noqa: E402
import app.core.limiter as _limiter_mod  # noqa: E402
_limiter_mod.limiter.limiter.storage = MemoryStorage()


# Also patch the *instance-based* limiter stored inside the router module.
# The router-level decorators capture their own Limiter instance at import
# time, so we need to patch that too.
import app.api.v1.offers as _offers_mod  # noqa: E402
_offers_mod.limiter.limiter.storage = MemoryStorage()


def _auth_header(user_id: str) -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token(subject=user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_offer(
    job_id: uuid.UUID,
    provider_type: str,
    provider_id: uuid.UUID,
    status: str = "pending",
    expires_at: datetime | None = None,
) -> ProviderOffer:
    return ProviderOffer(
        id=uuid.uuid4(),
        job_id=job_id,
        provider_type=provider_type,
        provider_id=provider_id,
        status=status,
        expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(minutes=15)),
        created_at=datetime.now(timezone.utc),
    )


# ── 401 / 403 ──────────────────────────────────────────────────────────


async def test_accept_requires_auth(client):
    """POST /offers/{id}/accept returns 401 without auth."""
    offer_id = uuid.uuid4()
    resp = await client.post(f"/api/offers/{offer_id}/accept")
    assert resp.status_code == 401


async def test_accept_forbidden_for_customer(client, test_user):
    """Customer role receives 403 — only mechanics/garages allowed."""
    headers = _auth_header(str(test_user.id))
    resp = await client.post(f"/api/offers/{uuid.uuid4()}/accept", headers=headers)
    assert resp.status_code == 403


# ── Successful accept (mechanic) ───────────────────────────────────────


async def test_mechanic_accepts_own_offer(
    client, db_session, test_mechanic, test_job,
):
    """Mechanic accepts a pending offer and the job is assigned."""
    offer = _make_offer(test_job.id, "mechanic", test_mechanic.id)
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/accept", headers=headers)
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "accepted"
    assert body["job_status"] == "assigned"
    assert body["id"] == str(offer.id)
    assert body["job_id"] == str(test_job.id)

    # Verify DB state
    await db_session.refresh(offer)
    assert offer.status == "accepted"

    await db_session.refresh(test_job)
    assert test_job.status == "assigned"
    assert test_job.assigned_mechanic_id == test_mechanic.id
    assert test_job.assigned_provider_type == "mechanic"


async def test_garage_accepts_own_offer(
    client, db_session, test_garage, test_job,
):
    """Garage accepts a pending offer and the job is assigned."""
    offer = _make_offer(test_job.id, "garage", test_garage.id)
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_garage.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/accept", headers=headers)
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "accepted"
    assert body["job_status"] == "assigned"

    await db_session.refresh(offer)
    assert offer.status == "accepted"

    await db_session.refresh(test_job)
    assert test_job.status == "assigned"
    assert test_job.assigned_garage_id == test_garage.id
    assert test_job.assigned_provider_type == "garage"


# ── Authorization failures ────────────────────────────────────────────


async def test_cannot_accept_other_providers_offer(
    client, db_session, test_mechanic, test_garage, test_job,
):
    """A mechanic cannot accept an offer meant for a garage."""
    offer = _make_offer(test_job.id, "garage", test_garage.id)
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/accept", headers=headers)
    assert resp.status_code == 404


async def test_cannot_accept_already_accepted_offer(
    client, db_session, test_mechanic, test_job,
):
    """Offers already accepted/declined return 409."""
    # Manually set the job to assigned so the offer can be "accepted"
    test_job.status = "assigned"
    await db_session.flush()

    offer = _make_offer(test_job.id, "mechanic", test_mechanic.id, status="accepted")
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/accept", headers=headers)
    assert resp.status_code == 409
    assert "already" in resp.text.lower()


async def test_expired_offer_returns_410(
    client, db_session, test_mechanic, test_job,
):
    """Expired offers cannot be accepted."""
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    offer = _make_offer(test_job.id, "mechanic", test_mechanic.id, expires_at=past)
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/accept", headers=headers)
    assert resp.status_code == 410


# ── Concurrency ────────────────────────────────────────────────────────


async def test_concurrent_accept_exactly_one_succeeds(
    client, db_session, test_mechanic, test_mechanic2, test_job,
):
    """Two mechanics accepting the same job — exactly one wins.

    The first accept succeeds; the second (sequential) gets a 409
    because the job is no longer in 'searching' state.  SQLite does
    not support true concurrent FOR UPDATE locks, so the test is
    sequential but validates the same atomicity invariant.
    """
    offer1 = _make_offer(test_job.id, "mechanic", test_mechanic.id)
    offer2 = _make_offer(test_job.id, "mechanic", test_mechanic2.id)
    db_session.add(offer1)
    db_session.add(offer2)
    await db_session.flush()

    headers1 = _auth_header(str(test_mechanic.user.id))
    headers2 = _auth_header(str(test_mechanic2.user.id))

    r1 = await client.post(f"/api/offers/{offer1.id}/accept", headers=headers1)
    assert r1.status_code == 200

    r2 = await client.post(f"/api/offers/{offer2.id}/accept", headers=headers2)
    assert r2.status_code == 409

    await db_session.refresh(test_job)
    assert test_job.status == "assigned"
    assert test_job.assigned_mechanic_id == test_mechanic.id


# ── Decline ────────────────────────────────────────────────────────────


async def test_decline_requires_auth(client):
    """POST /offers/{id}/decline returns 401 without auth."""
    offer_id = uuid.uuid4()
    resp = await client.post(f"/api/offers/{offer_id}/decline")
    assert resp.status_code == 401


async def test_mechanic_declines_own_offer(
    client, db_session, test_mechanic, test_job,
):
    """Mechanic declines a pending offer — job stays searching."""
    offer = _make_offer(test_job.id, "mechanic", test_mechanic.id)
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/decline", headers=headers)
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "declined"
    assert body["job_status"] == "searching"

    await db_session.refresh(offer)
    assert offer.status == "declined"

    # Job unaffected
    await db_session.refresh(test_job)
    assert test_job.status == "searching"


async def test_cannot_decline_other_providers_offer(
    client, db_session, test_mechanic, test_garage, test_job,
):
    """A mechanic cannot decline an offer meant for a garage."""
    offer = _make_offer(test_job.id, "garage", test_garage.id)
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/decline", headers=headers)
    assert resp.status_code == 404


async def test_cannot_decline_already_accepted_offer(
    client, db_session, test_mechanic, test_job,
):
    """Already accepted/declined offers return 409 on decline."""
    offer = _make_offer(test_job.id, "mechanic", test_mechanic.id, status="accepted")
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))
    resp = await client.post(f"/api/offers/{offer.id}/decline", headers=headers)
    assert resp.status_code == 409
