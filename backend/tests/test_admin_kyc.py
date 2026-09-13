"""Tests for the admin KYC review feature.

Covers GET /api/admin/kyc/pending (real document data, status filtering)
and PATCH /api/admin/kyc/{profile_type}/{id}/review (approve/reject with
note, role protection).
"""

import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-long-enough-for-pytest")

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "/api/admin/kyc"


async def _mk_user(db: AsyncSession, role: str, email: str | None = None) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email or f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x" * 60,
        role=role,
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()
    return user


async def _mk_mechanic(
    db: AsyncSession,
    user: User,
    *,
    aadhaar: str | None = None,
    license_url: str | None = None,
    kyc_status: str = "pending",
    verified: bool = False,
    note: str | None = None,
) -> Mechanic:
    m = Mechanic(
        id=uuid.uuid4(),
        user_id=user.id,
        full_name="Test Mechanic",
        phone="+919876543210",
        lat=11.0,
        lon=76.0,
        verified=verified,
        aadhaar_photo_url=aadhaar,
        license_photo_url=license_url,
        kyc_status=kyc_status,
        kyc_note=note,
    )
    db.add(m)
    await db.flush()
    return m


async def _mk_garage(
    db: AsyncSession,
    user: User,
    *,
    aadhaar: str | None = None,
    license_url: str | None = None,
    kyc_status: str = "pending",
    verified: bool = False,
) -> Garage:
    g = Garage(
        id=uuid.uuid4(),
        user_id=user.id,
        garage_name="Test Garage",
        owner_name="Garage Owner",
        lat=11.0,
        lon=76.0,
        verified=verified,
        aadhaar_photo_url=aadhaar,
        license_photo_url=license_url,
        kyc_status=kyc_status,
    )
    db.add(g)
    await db.flush()
    return g


async def _admin_headers(db: AsyncSession) -> dict:
    admin = await _mk_user(db, "admin", "admin@example.com")
    token = create_access_token(subject=str(admin.id))
    return {"Authorization": f"Bearer {token}"}


async def _mech_headers(db: AsyncSession, mech_user: User) -> dict:
    token = create_access_token(subject=str(mech_user.id))
    return {"Authorization": f"Bearer {token}"}


async def test_kyc_pending_requires_admin(db_session: AsyncSession, client: AsyncClient):
    """KYC1: Non-admin gets 403 on the review queue."""
    u = await _mk_user(db_session, "customer")
    res = await client.get(f"{BASE}/pending", headers=await _mech_headers(db_session, u))
    assert res.status_code == 403


async def test_kyc_pending_returns_real_documents(db_session: AsyncSession, client: AsyncClient):
    """KYC2: Queue returns real photo URLs + contact info, not fake doc names."""
    headers = await _admin_headers(db_session)
    u = await _mk_user(db_session, "mechanic")
    await _mk_mechanic(
        db_session,
        u,
        aadhaar="/static/uploads/aadhaar-1.jpg",
        license_url="/static/uploads/license-1.jpg",
        kyc_status="submitted",
    )
    await db_session.commit()

    res = await client.get(f"{BASE}/pending", headers=headers)
    assert res.status_code == 200
    apps = res.json()["applications"]
    assert len(apps) == 1
    app = apps[0]
    assert app["profileType"] == "mechanic"
    assert app["kycStatus"] == "submitted"
    assert app["email"] == u.email
    assert app["phone"] == "+919876543210"
    kinds = {d["kind"]: d["url"] for d in app["documents"]}
    assert kinds["aadhaar"] == "/static/uploads/aadhaar-1.jpg"
    assert kinds["license"] == "/static/uploads/license-1.jpg"


async def test_kyc_pending_defaults_to_actionable(db_session: AsyncSession, client: AsyncClient):
    """KYC3: Default queue shows pending+submitted only; filter reaches others."""
    headers = await _admin_headers(db_session)
    mu = await _mk_user(db_session, "mechanic")
    await _mk_mechanic(db_session, mu, kyc_status="submitted")
    vu = await _mk_user(db_session, "mechanic")
    await _mk_mechanic(db_session, vu, kyc_status="verified", verified=True)
    gu = await _mk_user(db_session, "garage")
    await _mk_garage(db_session, gu, aadhaar="/static/uploads/g.jpg", kyc_status="submitted")
    await db_session.commit()

    res = await client.get(f"{BASE}/pending", headers=headers)
    apps = res.json()["applications"]
    assert {a["kycStatus"] for a in apps} == {"submitted"}
    assert {a["profileType"] for a in apps} == {"mechanic", "garage"}

    res = await client.get(f"{BASE}/pending?status=verified", headers=headers)
    apps = res.json()["applications"]
    assert {a["kycStatus"] for a in apps} == {"verified"}


async def test_kyc_review_approve(db_session: AsyncSession, client: AsyncClient):
    """KYC4: Approve sets kyc_status=verified + verified=True."""
    headers = await _admin_headers(db_session)
    u = await _mk_user(db_session, "mechanic")
    m = await _mk_mechanic(db_session, u, aadhaar="/static/uploads/a.jpg", kyc_status="submitted")
    await db_session.commit()

    res = await client.patch(
        f"{BASE}/mechanic/{m.id}/review",
        json={"action": "approve"},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kycStatus"] == "verified"
    assert data["verified"] is True

    await db_session.refresh(m)
    assert m.kyc_status == "verified"
    assert m.verified is True


async def test_kyc_review_reject_with_note(db_session: AsyncSession, client: AsyncClient):
    """KYC5: Reject sets kyc_status=rejected, un-verifies, stores the note."""
    headers = await _admin_headers(db_session)
    u = await _mk_user(db_session, "mechanic")
    m = await _mk_mechanic(
        db_session, u, aadhaar="/static/uploads/a.jpg", kyc_status="submitted"
    )
    # Pretend it was verified before — reject must un-verify.
    m.verified = True
    await db_session.commit()

    res = await client.patch(
        f"{BASE}/mechanic/{m.id}/review",
        json={"action": "reject", "note": "Aadhaar photo is blurred"},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kycStatus"] == "rejected"
    assert data["verified"] is False
    assert data["kycNote"] == "Aadhaar photo is blurred"

    await db_session.refresh(m)
    assert m.kyc_status == "rejected"
    assert m.verified is False
    assert m.kyc_note == "Aadhaar photo is blurred"


async def test_kyc_review_resubmit_after_reject(db_session: AsyncSession, client: AsyncClient):
    """KYC6: Provider re-upload via PUT /profile/me resets rejected → submitted."""
    headers = await _admin_headers(db_session)
    u = await _mk_user(db_session, "mechanic")
    m = await _mk_mechanic(
        db_session, u, aadhaar="/static/uploads/old.jpg", kyc_status="rejected", note="Blurry"
    )
    await db_session.commit()

    provider_headers = await _mech_headers(db_session, u)
    res = await client.put(
        "/api/profile/me",
        json={"aadhaarPhotoUrl": "/static/uploads/new.jpg"},
        headers=provider_headers,
    )
    assert res.status_code == 200
    await db_session.refresh(m)
    assert m.kyc_status == "submitted"
    assert m.kyc_note is None
    assert m.aadhaar_photo_url == "/static/uploads/new.jpg"


async def test_kyc_review_unknown_profile_type(db_session: AsyncSession, client: AsyncClient):
    """KYC7: Unknown profile_type is a 422, not a crash."""
    headers = await _admin_headers(db_session)
    res = await client.patch(
        f"{BASE}/customer/{uuid.uuid4()}/review",
        json={"action": "approve"},
        headers=headers,
    )
    assert res.status_code == 422


async def test_kyc_review_missing_provider(db_session: AsyncSession, client: AsyncClient):
    """KYC8: Reviewing a nonexistent provider 404s."""
    headers = await _admin_headers(db_session)
    res = await client.patch(
        f"{BASE}/mechanic/{uuid.uuid4()}/review",
        json={"action": "approve"},
        headers=headers,
    )
    assert res.status_code == 404
