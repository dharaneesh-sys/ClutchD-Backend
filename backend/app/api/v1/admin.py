from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, require_admin
from app.models.dispute import Dispute
from app.models.enums import DisputeStatus, UserRole
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.payment import Payment
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Type alias for admin-protected routes ──────────────────────
AdminUser = Annotated[User, Depends(require_admin)]


# ── Users ─────────────────────────────────────────────────────
@router.get("/users")
async def list_users(
    db: DbSession,
    user: AdminUser,
    role: str | None = Query(None, pattern="^(customer|mechanic|garage|admin)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    q = select(User)
    if role:
        q = q.where(User.role == role)
    q = q.offset(skip).limit(limit).order_by(User.created_at.desc())
    result = await db.execute(q)
    users = result.scalars().all()
    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "role": u.role,
                "isActive": u.is_active,
                "createdAt": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    }


# ── Verification ──────────────────────────────────────────────
class VerifyBody(BaseModel):
    verified: bool = True


@router.patch("/mechanic/{mechanic_id}/verify")
async def verify_mechanic(mechanic_id: UUID, body: VerifyBody, db: DbSession, user: AdminUser):
    result = await db.execute(select(Mechanic).where(Mechanic.id == mechanic_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Mechanic not found")
    m.verified = body.verified
    await db.flush()
    return {"ok": True, "verified": m.verified}


@router.patch("/garage/{garage_id}/verify")
async def verify_garage(garage_id: UUID, body: VerifyBody, db: DbSession, user: AdminUser):
    result = await db.execute(select(Garage).where(Garage.id == garage_id))
    g = result.scalar_one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Garage not found")
    g.verified = body.verified
    await db.flush()
    return {"ok": True, "verified": g.verified}


# ── Analytics ─────────────────────────────────────────────────
@router.get("/analytics")
async def analytics(db: DbSession, user: AdminUser):
    users = await db.execute(select(func.count(User.id)))
    jobs = await db.execute(select(func.count(Job.id)))
    payments = await db.execute(select(func.coalesce(func.sum(Payment.amount), 0)))
    mechanics = await db.execute(select(func.count(Mechanic.id)))
    garages = await db.execute(select(func.count(Garage.id)))
    return {
        "totalUsers": users.scalar(),
        "totalJobs": jobs.scalar(),
        "totalRevenue": payments.scalar(),
        "totalMechanics": mechanics.scalar(),
        "totalGarages": garages.scalar(),
    }


# ── Disputes ──────────────────────────────────────────────────
@router.get("/disputes")
async def list_disputes(
    db: DbSession,
    user: AdminUser,
    status_filter: str | None = Query(None, alias="status"),
):
    q = select(Dispute)
    if status_filter:
        try:
            ds = DisputeStatus(status_filter)
            q = q.where(Dispute.status == ds.value)
        except ValueError:
            pass
    q = q.order_by(Dispute.created_at.desc())
    result = await db.execute(q)
    rows = result.scalars().all()
    return {
        "disputes": [
            {
                "id": str(d.id),
                "jobId": str(d.job_id),
                "reason": d.reason,
                "status": d.status,
                "createdAt": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]
    }


class DisputeUpdateBody(BaseModel):
    status: str = Field(pattern="^(open|investigating|resolved|dismissed)$")
    resolution: str | None = Field(None, max_length=2000)


@router.patch("/disputes/{dispute_id}")
async def update_dispute(
    dispute_id: UUID,
    body: DisputeUpdateBody,
    db: DbSession,
    user: AdminUser,
):
    result = await db.execute(select(Dispute).where(Dispute.id == dispute_id))
    d = result.scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Dispute not found")
    d.status = body.status
    if body.resolution:
        d.resolution = body.resolution
    await db.flush()
    return {"ok": True}


# ── Pricing ───────────────────────────────────────────────────
class PricingBody(BaseModel):
    service_type: str = Field(min_length=1, max_length=64)
    min_price: int = Field(ge=0, le=50000000)
    max_price: int = Field(ge=0, le=50000000)


@router.get("/pricing")
async def get_pricing(db: DbSession, user: AdminUser):
    # TODO: Return from a pricing table when implemented
    return {"pricing": []}


@router.post("/pricing")
async def set_pricing(body: PricingBody, db: DbSession, user: AdminUser):
    # TODO: Upsert pricing row when pricing table is implemented
    return {"ok": True, "service_type": body.service_type}
