from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.redis_client import get_redis
from app.models.dispute import Dispute
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin(user: User) -> None:
    if user.role != "admin" and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/users")
async def list_users(db: DbSession, user: CurrentUser, limit: int = Query(50, le=200)):
    _admin(user)
    r = await db.execute(select(User).order_by(User.created_at.desc()).limit(limit))
    rows = r.scalars().all()
    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in rows
        ]
    }


@router.patch("/mechanics/{mechanic_id}/verify")
async def verify_mechanic(mechanic_id: UUID, db: DbSession, user: CurrentUser, verified: bool = True):
    _admin(user)
    r = await db.execute(select(Mechanic).where(Mechanic.id == mechanic_id))
    m = r.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Mechanic not found")
    m.verified = verified
    await db.flush()
    return {"ok": True, "verified": verified}


@router.patch("/garages/{garage_id}/verify")
async def verify_garage(garage_id: UUID, db: DbSession, user: CurrentUser, verified: bool = True):
    _admin(user)
    r = await db.execute(select(Garage).where(Garage.id == garage_id))
    g = r.scalar_one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Garage not found")
    g.verified = verified
    await db.flush()
    return {"ok": True, "verified": verified}


@router.get("/analytics")
async def analytics(db: DbSession, user: CurrentUser):
    _admin(user)
    nu = await db.scalar(select(func.count()).select_from(User))
    nj = await db.scalar(select(func.count()).select_from(Job))
    nc = await db.scalar(select(func.count()).select_from(Job).where(Job.status == "completed"))
    nd = await db.scalar(select(func.count()).select_from(Dispute).where(Dispute.status == "open"))
    return {
        "users": nu or 0,
        "jobs": nj or 0,
        "completed_jobs": nc or 0,
        "open_disputes": nd or 0,
    }


@router.get("/disputes")
async def list_disputes(db: DbSession, user: CurrentUser):
    _admin(user)
    r = await db.execute(select(Dispute).order_by(Dispute.created_at.desc()))
    rows = r.scalars().all()
    return {
        "disputes": [
            {
                "id": str(d.id),
                "job_id": str(d.job_id),
                "status": d.status,
                "notes": d.notes,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]
    }


@router.patch("/disputes/{dispute_id}")
async def update_dispute(
    dispute_id: UUID,
    db: DbSession,
    user: CurrentUser,
    status: str = Query(..., pattern="^(open|resolved)$"),
    notes: str | None = None,
):
    _admin(user)
    r = await db.execute(select(Dispute).where(Dispute.id == dispute_id))
    d = r.scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Dispute not found")
    d.status = status
    if notes is not None:
        d.notes = notes
    await db.flush()
    return {"ok": True}


@router.get("/pricing")
async def get_pricing(user: CurrentUser):
    _admin(user)
    r = await get_redis()
    v = await r.get("pricing:multiplier")
    return {"base_multiplier": float(v) if v else 1.0}


class PricingBody(BaseModel):
    multiplier: float = Field(gt=0, le=10)


@router.put("/pricing")
async def set_pricing(body: PricingBody, user: CurrentUser):
    _admin(user)
    r = await get_redis()
    await r.set("pricing:multiplier", str(body.multiplier))
    return {"base_multiplier": body.multiplier}
