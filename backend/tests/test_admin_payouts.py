"""Tests for GET /api/admin/payouts — real ledger from jobs + captured payments.

Guards against the old client-side demo ledger (Rajesh M. & co.): the
endpoint must return only providers that actually have jobs with money,
with amounts derived from Job.service_amount and captured/succeeded
payments.
"""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.payment import Payment
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "/api/admin/payouts"


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


async def _admin_headers(db: AsyncSession) -> dict:
    admin = await _mk_user(db, "admin")
    token = create_access_token(subject=str(admin.id))
    return {"Authorization": f"Bearer {token}"}


async def _mk_mechanic(db: AsyncSession, name: str = "Ledger Mech") -> tuple[User, Mechanic]:
    u = await _mk_user(db, "mechanic")
    m = Mechanic(
        id=uuid.uuid4(),
        user_id=u.id,
        full_name=name,
        phone="+919876543210",
        lat=11.0,
        lon=76.0,
    )
    db.add(m)
    await db.flush()
    return u, m


async def _mk_job(db: AsyncSession, customer: User, mech: Mechanic, *, price: float, status: str) -> Job:
    job = Job(
        id=uuid.uuid4(),
        user_id=customer.id,
        issue_tag="battery",
        description="Battery dead",
        status=status,
        customer_lat=11.0,
        customer_lon=76.0,
        service_amount=price,
        total_amount=price + 40,
        assigned_mechanic_id=mech.id,
        assigned_type="mechanic",
    )
    db.add(job)
    await db.flush()
    return job


async def test_payouts_requires_admin(client: AsyncClient, db_session: AsyncSession):
    u = await _mk_user(db_session, "customer")
    token = create_access_token(subject=str(u.id))
    res = await client.get(BASE, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


async def test_payouts_empty_when_no_jobs(client: AsyncClient, db_session: AsyncSession):
    headers = await _admin_headers(db_session)
    await db_session.commit()
    res = await client.get(BASE, headers=headers)
    assert res.status_code == 200
    assert res.json() == {"payouts": []}


async def test_payouts_real_amounts_no_demo_rows(client: AsyncClient, db_session: AsyncSession):
    """One mechanic, two completed jobs (one cash-collected): ledger shows real sums."""
    headers = await _admin_headers(db_session)
    _, mech = await _mk_mechanic(db_session, "Real Mechanic")
    customer = await _mk_user(db_session, "customer")

    j1 = await _mk_job(db_session, customer, mech, price=500.0, status="completed")
    j2 = await _mk_job(db_session, customer, mech, price=300.0, status="completed")
    # Cash collected on j2 only.
    db_session.add(
        Payment(
            job_id=j2.id,
            user_id=customer.id,
            amount=34000,
            currency="inr",
            provider="manual",
            status="captured",
            method="cash",
        )
    )
    await db_session.commit()

    res = await client.get(BASE, headers=headers)
    assert res.status_code == 200
    body = res.json()
    rows = body["payouts"]
    assert len(rows) == 1

    row = rows[0]
    assert row["mechanicName"] == "Real Mechanic"
    assert row["providerType"] == "mechanic"
    assert row["totalJobs"] == 2
    assert row["completedJobs"] == 2
    assert row["pendingJobs"] == 0
    assert row["totalEarned"] == pytest.approx(800.0)
    assert row["pendingAmount"] == pytest.approx(800.0)
    # The demo names must never come back.
    assert "Rajesh" not in row["mechanicName"]


async def test_payouts_groups_garages_separately(client: AsyncClient, db_session: AsyncSession):
    headers = await _admin_headers(db_session)
    gu = await _mk_user(db_session, "garage")
    g = Garage(
        id=uuid.uuid4(),
        user_id=gu.id,
        garage_name="Payout Garage",
        owner_name="Owner",
        lat=11.0,
        lon=76.0,
    )
    db_session.add(g)
    customer = await _mk_user(db_session, "customer")
    job = Job(
        id=uuid.uuid4(),
        user_id=customer.id,
        issue_tag="engine",
        description="Engine noise",
        status="completed",
        customer_lat=11.0,
        customer_lon=76.0,
        service_amount=1200.0,
        total_amount=1240.0,
        assigned_garage_id=g.id,
        assigned_type="garage",
    )
    db_session.add(job)
    await db_session.commit()

    res = await client.get(BASE, headers=headers)
    assert res.status_code == 200
    rows = res.json()["payouts"]
    assert len(rows) == 1
    assert rows[0]["providerType"] == "garage"
    assert rows[0]["mechanicName"] == "Payout Garage"
    assert rows[0]["pendingAmount"] == pytest.approx(1200.0)
