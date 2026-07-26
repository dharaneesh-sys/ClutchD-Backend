"""Tests for GET /providers/offers endpoint.

Covers authentication (401/403), role-based access, and filtering
(own offers, expired exclusion, pagination).

All ProviderOffer records are created directly in test fixtures to
avoid the PostgreSQL-specific PGInsert path in the production code.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.provider_offer import ProviderOffer

pytestmark = pytest.mark.asyncio


# ── Token helpers ──────────────────────────────────────────────────────

# Enable debug mode to bypass the JWT weak-secret check in
# ``app.core.security._check_secret``.
from app.core.config import get_settings  # noqa: E402
get_settings().debug = True

# Patch the rate limiter storage to use in-memory (no Redis needed).
# The ``@limiter.limit(...)`` decorators on API endpoints captured the
# original slowapi Limiter instance at import time, so we replace the
# underlying ``limits.Limiter.storage`` with a ``MemoryStorage``.
from limits.storage import MemoryStorage  # noqa: E402
import app.core.limiter as _limiter_mod  # noqa: E402
_limiter_mod.limiter.limiter.storage = MemoryStorage()


def _auth_header(user_id: str) -> dict[str, str]:
    """Return ``Authorization`` header dict with a valid JWT for *user_id*."""
    from app.core.security import create_access_token

    token = create_access_token(subject=user_id)
    return {"Authorization": f"Bearer {token}"}


# ── Auth tests (T18, T19) ──────────────────────────────────────────────


async def test_offers_requires_auth(client):
    """T18: GET /providers/offers returns 401 without auth."""
    response = await client.get("/api/providers/offers")
    assert response.status_code == 401


async def test_offers_forbidden_for_customer(client, test_user):
    """T19: customer role receives 403 — only mechanics/garages allowed."""
    headers = _auth_header(str(test_user.id))
    response = await client.get("/api/providers/offers", headers=headers)
    assert response.status_code == 403


# ── Provider sees own offers (T20, T21) ────────────────────────────────


async def test_mechanic_sees_own_offers(
    client, db_session, test_mechanic, test_job,
):
    """T20: mechanic user sees only their own offers."""
    # Create a ProviderOffer for this mechanic
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

    headers = _auth_header(str(test_mechanic.user.id))
    response = await client.get("/api/providers/offers", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert len(data["offers"]) >= 1
    assert data["offers"][0]["providerId"] == str(test_mechanic.id)
    assert data["offers"][0]["jobId"] == str(test_job.id)


async def test_garage_sees_own_offers(
    client, db_session, test_garage, test_job,
):
    """T21: garage user sees only their own offers."""
    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job.id,
        provider_type="garage",
        provider_id=test_garage.id,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)
    await db_session.flush()

    headers = _auth_header(str(test_garage.user.id))
    response = await client.get("/api/providers/offers", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert len(data["offers"]) >= 1
    assert data["offers"][0]["providerId"] == str(test_garage.id)


# ── Pagination (T22) ──────────────────────────────────────────────────


async def test_offers_pagination(
    client, db_session, test_mechanic, test_job,
):
    """T22: limit and offset parameters paginate results correctly.

    Creates 2 separate jobs so that 2 distinct offers exist for the
    same mechanic, then verifies limit=1 returns only 1 offer.
    """
    from app.models.job import Job

    # Create a second job so we can have two distinct offers
    job2 = Job(
        id=uuid.uuid4(),
        user_id=test_job.user_id,
        issue_tag="flat_tire",
        description="Second job for pagination",
        request_type="auto",
        status="searching",
        customer_lat=28.6139,
        customer_lon=77.2090,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(job2)
    await db_session.flush()

    # Create two offers for the same mechanic on different jobs
    offer1 = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job.id,
        provider_type="mechanic",
        provider_id=test_mechanic.id,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        created_at=datetime.now(timezone.utc),
    )
    offer2 = ProviderOffer(
        id=uuid.uuid4(),
        job_id=job2.id,
        provider_type="mechanic",
        provider_id=test_mechanic.id,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([offer1, offer2])
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))

    # Fetch with limit=1, offset=0 → 1 result
    r1 = await client.get(
        "/api/providers/offers?limit=1&offset=0", headers=headers,
    )
    assert r1.status_code == 200
    assert len(r1.json()["offers"]) == 1

    # Fetch with limit=1, offset=1 → 1 different result
    r2 = await client.get(
        "/api/providers/offers?limit=1&offset=1", headers=headers,
    )
    assert r2.status_code == 200
    assert len(r2.json()["offers"]) == 1
    # The two results should have different IDs
    assert r1.json()["offers"][0]["id"] != r2.json()["offers"][0]["id"]


# ── Expired offers (T23) ──────────────────────────────────────────────


async def test_expired_offers_excluded_by_default(
    client, db_session, test_mechanic, test_job,
):
    """T23: expired offers (expires_at < now) excluded from default response.

    The endpoint's default filter excludes offers whose ``expires_at``
    is in the past (``expires_at <= func.now()``).
    """
    # Create an offer that expired 1 hour ago
    expired = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job.id,
        provider_type="mechanic",
        provider_id=test_mechanic.id,
        status="pending",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(expired)
    await db_session.flush()

    headers = _auth_header(str(test_mechanic.user.id))

    # Default query (no status filter) — expired offers are excluded
    response = await client.get("/api/providers/offers", headers=headers)
    assert response.status_code == 200
    data = response.json()
    # The expired offer should NOT be in the default list
    offer_ids = [o["id"] for o in data["offers"]]
    assert str(expired.id) not in offer_ids

    # Explicit ?status=expired — expired offer IS included
    response_expired = await client.get(
        "/api/providers/offers?status=expired", headers=headers,
    )
    assert response_expired.status_code == 200
    data_exp = response_expired.json()
    exp_ids = [o["id"] for o in data_exp["offers"]]
    assert str(expired.id) in exp_ids
