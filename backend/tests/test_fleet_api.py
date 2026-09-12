"""Tests for the Fleet API endpoints.

Covers POST /api/fleet/register (public, dedupe, GSTIN validation),
GET /api/fleet/my-fleet (auth-scoped listing), and GET /api/fleet/{id}
(owner/admin scoping).
"""

import os
import uuid

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-long-enough-for-pytest")

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.fleet import Fleet
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "/api/fleet"

# Rate-limiter patch: the decorators captured the Limiter at import time,
# so replace the underlying storage to isolate each test run.
from limits.storage import MemoryStorage  # noqa: E402
import app.core.limiter as _limiter_mod  # noqa: E402
import app.api.v1.fleet as _fleet_mod  # noqa: E402
_limiter_mod.limiter.limiter.storage = MemoryStorage()
_fleet_mod.limiter.limiter.storage = MemoryStorage()


@pytest.fixture(autouse=True)
def _fresh_rate_limit_storage():
    """Reset rate-limit counters so tests don't trip the 5/minute cap."""
    try:
        _fleet_mod.limiter.limiter.storage.reset()
    except Exception:
        pass
    yield


def _body(**overrides) -> dict:
    b = {
        "companyName": "Acme Logistics",
        "fleetType": "logistics",
        "fleetSize": "6-10",
        "contactName": "Priya Sharma",
        "contactEmail": "priya@acme.example",
        "contactPhone": "+919876543210",
        "businessAddress": "12 MG Road, Coimbatore",
    }
    b.update(overrides)
    return b


async def _auth_header(test_user: User) -> dict:
    token = create_access_token(subject=str(test_user.id))
    return {"Authorization": f"Bearer {token}"}


async def test_register_fleet_public(db_session: AsyncSession, client: AsyncClient):
    """FLEET1: Public registration returns 201 with server defaults."""
    res = await client.post(f"{BASE}/register", json=_body())
    assert res.status_code == 201
    data = res.json()
    assert data["companyName"] == "Acme Logistics"
    assert data["tier"] == "bronze"
    assert data["discountRate"] == 5
    assert data["priorityDispatch"] is True
    assert data["id"]


async def test_register_fleet_links_existing_user(
    db_session: AsyncSession, client: AsyncClient, test_user: User
):
    """FLEET2: Registration with an existing account email links user_id."""
    res = await client.post(
        f"{BASE}/register", json=_body(contactEmail=test_user.email)
    )
    assert res.status_code == 201
    row = res.json()
    stored = await db_session.get(Fleet, __import__("uuid").UUID(row["id"]))
    assert stored is not None
    assert str(stored.user_id) == str(test_user.id)


async def test_register_fleet_duplicate_409(db_session: AsyncSession, client: AsyncClient):
    """FLEET3: Same company + email twice returns 409."""
    await client.post(f"{BASE}/register", json=_body())
    res = await client.post(f"{BASE}/register", json=_body())
    assert res.status_code == 409


async def test_register_fleet_bad_gstin_422(db_session: AsyncSession, client: AsyncClient):
    """FLEET4: Malformed GSTIN returns 422."""
    res = await client.post(f"{BASE}/register", json=_body(gstin="NOT-A-GSTIN"))
    assert res.status_code == 422


async def test_register_fleet_valid_gstin(db_session: AsyncSession, client: AsyncClient):
    """FLEET5: Correctly formatted GSTIN is accepted."""
    res = await client.post(f"{BASE}/register", json=_body(gstin="33AABCU9603R1ZL"))
    assert res.status_code == 201
    assert res.json()["gstin"] == "33AABCU9603R1ZL"


async def test_my_fleet_requires_auth(client: AsyncClient):
    """FLEET6: /my-fleet without a token returns 401."""
    res = await client.get(f"{BASE}/my-fleet")
    assert res.status_code == 401


async def test_my_fleet_lists_own_registrations(
    db_session: AsyncSession, client: AsyncClient, test_user: User
):
    """FLEET7: /my-fleet returns fleets linked by user_id or contact email."""
    headers = await _auth_header(test_user)
    await client.post(f"{BASE}/register", json=_body(contactEmail=test_user.email))
    res = await client.get(f"{BASE}/my-fleet", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["fleets"][0]["contactEmail"] == test_user.email


async def test_get_fleet_owner_only(
    db_session: AsyncSession, client: AsyncClient, test_user: User
):
    """FLEET8: A different authenticated user cannot read someone's fleet."""
    await client.post(f"{BASE}/register", json=_body())
    stranger = User(
        email=f"stranger-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="x",
        role="customer",
    )
    db_session.add(stranger)
    await db_session.flush()
    stoken = create_access_token(subject=str(stranger.id))

    # The registration was created anonymously (no user link), so read it
    # from the DB directly instead of the auth-scoped /my-fleet listing.
    from sqlalchemy import select
    row = await db_session.execute(select(Fleet).where(Fleet.company_name == "Acme Logistics"))
    fleet = row.scalar_one()
    fleet_id = str(fleet.id)

    res = await client.get(
        f"{BASE}/{fleet_id}", headers={"Authorization": f"Bearer {stoken}"}
    )
    assert res.status_code == 403
