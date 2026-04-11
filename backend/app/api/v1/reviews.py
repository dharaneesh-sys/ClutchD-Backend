from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models.enums import UserRole
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.review import Review
from app.schemas.review import ReviewCreate

router = APIRouter(tags=["reviews"])


async def _recompute_mechanic_rating(db, mechanic_id: UUID) -> None:
    r = await db.execute(select(func.avg(Review.rating)).where(Review.target_mechanic_id == mechanic_id))
    avg = r.scalar()
    if avg is not None:
        m = await db.execute(select(Mechanic).where(Mechanic.id == mechanic_id))
        mech = m.scalar_one_or_none()
        if mech:
            mech.rating = float(avg)
            await db.flush()


async def _recompute_garage_rating(db, garage_id: UUID) -> None:
    r = await db.execute(select(func.avg(Review.rating)).where(Review.target_garage_id == garage_id))
    avg = r.scalar()
    if avg is not None:
        g = await db.execute(select(Garage).where(Garage.id == garage_id))
        gar = g.scalar_one_or_none()
        if gar:
            gar.rating = float(avg)
            await db.flush()


@router.post("/reviews")
async def create_review(body: ReviewCreate, db: DbSession, user: CurrentUser):
    if user.role != UserRole.customer.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customers only")
    jr = await db.execute(select(Job).where(Job.id == body.job_id))
    job = jr.scalar_one_or_none()
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job must be completed before review")

    target_type = job.assigned_type or "mechanic"
    tm, tg = None, None
    if target_type == "mechanic" and job.assigned_mechanic_id:
        tm = job.assigned_mechanic_id
    elif target_type == "garage" and job.assigned_garage_id:
        tg = job.assigned_garage_id
    else:
        raise HTTPException(status_code=400, detail="No assignee to review")

    rev = Review(
        job_id=job.id,
        reviewer_user_id=user.id,
        rating=body.rating,
        comment=body.comment,
        target_type=target_type,
        target_mechanic_id=tm,
        target_garage_id=tg,
    )
    db.add(rev)
    await db.flush()
    if tm:
        await _recompute_mechanic_rating(db, tm)
    if tg:
        await _recompute_garage_rating(db, tg)
    return {"id": str(rev.id), "ok": True}


@router.get("/ratings/{entity_id}")
async def get_ratings(
    entity_id: UUID,
    db: DbSession,
    entity_type: str = Query(..., pattern="^(mechanic|garage)$"),
):
    if entity_type == "garage":
        q = select(Review).where(Review.target_garage_id == entity_id)
    else:
        q = select(Review).where(Review.target_mechanic_id == entity_id)
    r = await db.execute(q)
    rows = r.scalars().all()
    if not rows:
        return {"average": None, "count": 0, "reviews": []}
    avg = sum(x.rating for x in rows) / len(rows)
    return {
        "average": round(avg, 2),
        "count": len(rows),
        "reviews": [
            {"rating": x.rating, "comment": x.comment, "createdAt": x.created_at.isoformat() if x.created_at else None}
            for x in rows
        ],
    }
