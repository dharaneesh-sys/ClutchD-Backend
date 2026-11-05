"""Partner billing: incentive capture, rent lifecycle, role isolation, onboarding."""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.billing import ProviderLedgerEntry
from app.models.job import Job
from app.models.payment import Payment
from app.models.user import User
from app.services import billing_service as svc
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


def _h(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id), {'role': user.role})}"}


@pytest.fixture(autouse=True)
def billing_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "billing_enabled", True)


@pytest_asyncio.fixture
async def admin(db_session) -> User:
    u = User(id=uuid.uuid4(), email="billing-admin@example.com", password_hash="x", role="admin",
             is_active=True, is_superuser=True, created_at=datetime.now(timezone.utc))
    db_session.add(u)
    await db_session.commit()
    return u


async def _paid_job(db, customer, *, mechanic=None, garage=None, rupees=1000.0) -> Job:
    job = Job(id=uuid.uuid4(), user_id=customer.id, issue_tag="flat_tire", description="x",
              request_type="auto", status="completed", customer_lat=1.0, customer_lon=1.0,
              service_amount=rupees,
              assigned_mechanic_id=mechanic.id if mechanic else None,
              assigned_garage_id=garage.id if garage else None,
              created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db.add(job)
    await db.flush()
    db.add(Payment(id=uuid.uuid4(), job_id=job.id, user_id=customer.id, amount=int(rupees * 100),
                   provider="manual", status="captured", method="cash"))
    await db.commit()
    return job


async def test_incentive_5pct_once(db_session, test_user, test_mechanic):
    job = await _paid_job(db_session, test_user, mechanic=test_mechanic, rupees=1000.0)
    first = await svc.record_capture(db_session, job)
    assert first is not None and first.amount_paise == 5000 and first.kind == "incentive"
    assert await svc.record_capture(db_session, job) is None  # idempotent
    n = (await db_session.execute(select(func.count()).select_from(ProviderLedgerEntry))).scalar()
    assert n == 1


async def test_no_incentive_when_flag_off(db_session, test_user, test_mechanic, monkeypatch):
    monkeypatch.setattr(get_settings(), "billing_enabled", False)
    job = await _paid_job(db_session, test_user, mechanic=test_mechanic)
    assert await svc.record_capture(db_session, job) is None


async def test_rent_lifecycle_and_isolation(client: AsyncClient, db_session, admin, test_garage, test_mechanic):
    inv, created = await svc.issue_rent_invoice(db_session, test_garage, *svc.month_period(svc.today_local()))
    assert created and inv.amount_paise == 49900
    await db_session.commit()
    again, created2 = await svc.issue_rent_invoice(db_session, test_garage, *svc.month_period(svc.today_local()))
    assert not created2 and again.id == inv.id

    g = _h(test_garage.user)
    r = await client.get("/api/providers/rent/invoices", headers=g)
    assert r.status_code == 200 and r.json()["total"] == 1

    # mechanics have no rent; admin-only routes reject partners
    assert (await client.get("/api/providers/rent/invoices", headers=_h(test_mechanic.user))).status_code == 403
    assert (await client.get("/api/admin/rent/config", headers=g)).status_code == 403

    r = await client.post(f"/api/providers/rent/invoices/{inv.id}/pay", json={"reference": "UPI123456"}, headers=g)
    assert r.status_code == 200 and r.json()["status"] == "payment_pending"

    r = await client.post(f"/api/admin/rent/invoices/{inv.id}/confirm", headers=_h(admin))
    assert r.status_code == 200 and r.json()["status"] == "paid"
    led = await client.get("/api/providers/ledger", headers=g)
    assert led.json()["balance_paise"] == 0  # charge and payment net out

    r = await client.post(f"/api/admin/rent/invoices/{inv.id}/waive", json={"reason": "late waiver"}, headers=_h(admin))
    assert r.status_code == 409


async def test_waive_needs_reason_and_foreign_invoice_404(client, db_session, admin, test_garage):
    from app.models.garage import Garage
    from app.models.user import User as U
    other_u = U(id=uuid.uuid4(), email="g2@example.com", password_hash="x", role="garage", is_active=True,
                is_superuser=False, created_at=datetime.now(timezone.utc))
    db_session.add(other_u)
    await db_session.flush()
    other = Garage(id=uuid.uuid4(), user_id=other_u.id, garage_name="G2", owner_name="o", phone="+911234567899",
                   services=["x"], mechanic_count=1, operating_hours="9", location_address="a", lat=1.0, lon=1.0,
                   rating=4.0, verified=True, penalized=False, created_at=datetime.now(timezone.utc))
    db_session.add(other)
    await db_session.commit()
    inv, _ = await svc.issue_rent_invoice(db_session, test_garage, *svc.month_period(svc.today_local()))
    await db_session.commit()

    r = await client.post(f"/api/providers/rent/invoices/{inv.id}/pay", json={"reference": "UPI123456"}, headers=_h(other_u))
    assert r.status_code == 404
    r = await client.post(f"/api/admin/rent/invoices/{inv.id}/waive", json={"reason": ""}, headers=_h(admin))
    assert r.status_code == 422
    r = await client.post(f"/api/admin/rent/invoices/{inv.id}/waive", json={"reason": "pilot partner"}, headers=_h(admin))
    assert r.status_code == 200 and r.json()["status"] == "waived"


async def test_incentive_rule_admin_and_summary(client, db_session, admin, test_user, test_mechanic):
    r = await client.put("/api/admin/incentives/rules", json={"rate_bps": 1000}, headers=_h(admin))
    assert r.status_code == 200 and r.json()["rate_bps"] == 1000
    assert (await client.put("/api/admin/incentives/rules", json={"rate_bps": 0}, headers=_h(admin))).status_code == 422
    job = await _paid_job(db_session, test_user, mechanic=test_mechanic, rupees=500.0)
    await svc.record_capture(db_session, job)
    await db_session.commit()
    s = (await client.get("/api/providers/incentives", headers=_h(test_mechanic.user))).json()
    assert s["rate_bps"] == 1000 and s["earned_paise"] == 5000


async def test_onboarding_roundtrip_and_customer_blocked(client, test_user, test_mechanic):
    h = _h(test_mechanic.user)
    assert (await client.get("/api/providers/onboarding", headers=h)).json()["step"] == "profile"
    r = await client.patch("/api/providers/onboarding", json={"step": "done"}, headers=h)
    assert r.status_code == 200 and r.json()["completed_at"] is not None
    assert (await client.patch("/api/providers/onboarding", json={"step": "bogus"}, headers=h)).status_code == 422
    assert (await client.get("/api/providers/onboarding", headers=_h(test_user))).status_code == 403
