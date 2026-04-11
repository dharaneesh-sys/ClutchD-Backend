from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models.enums import UserRole
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.payment import Payment
from app.services import matching
from app.services.user_payload import user_to_frontend_dict

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/nearby")
async def nearby_providers(
    db: DbSession,
    lat: float = Query(...),
    lng: float = Query(...),
    issue: str | None = Query(None, description="Optional issue tag for expertise/service overlap"),
):
    mechs = await matching.nearest_mechanics(db, lat, lng, limit=25, issue_tag=issue)
    gars = await matching.nearest_garages(db, lat, lng, limit=25, issue_tag=issue)
    return {
        "mechanics": [matching.mechanic_to_map_dict(m) for m in mechs],
        "garages": [matching.garage_to_map_dict(g) for g in gars],
    }


# ── Profile Update ────────────────────────────────────
class ProfileUpdateBody(BaseModel):
    fullName: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=15)
    location: str | None = Field(None, max_length=500)
    expertise: list[str] | None = Field(None, max_length=20)
    services: list[str] | None = Field(None, max_length=50)
    garageName: str | None = Field(None, max_length=200)
    ownerName: str | None = Field(None, max_length=100)
    operatingHours: str | None = Field(None, max_length=50)
    mechanicCount: int | None = Field(None, ge=0, le=999)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


@router.patch("/profile")
async def update_profile(body: ProfileUpdateBody, db: DbSession, user: CurrentUser):
    if user.role == UserRole.mechanic.value:
        r = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        mech = r.scalar_one_or_none()
        if not mech:
            raise HTTPException(status_code=404, detail="Mechanic profile not found")
        if body.fullName is not None:
            mech.full_name = body.fullName
        if body.phone is not None:
            mech.phone = body.phone
        if body.location is not None:
            mech.location_address = body.location
        if body.expertise is not None:
            mech.expertise = body.expertise
        if body.latitude is not None:
            mech.lat = body.latitude
        if body.longitude is not None:
            mech.lon = body.longitude
        await db.flush()

    elif user.role == UserRole.garage.value:
        r = await db.execute(select(Garage).where(Garage.user_id == user.id))
        garage = r.scalar_one_or_none()
        if not garage:
            raise HTTPException(status_code=404, detail="Garage profile not found")
        if body.garageName is not None:
            garage.garage_name = body.garageName
        if body.ownerName is not None:
            garage.owner_name = body.ownerName
        if body.phone is not None:
            garage.phone = body.phone
        if body.location is not None:
            garage.location_address = body.location
        if body.services is not None:
            garage.services = body.services
        if body.operatingHours is not None:
            garage.operating_hours = body.operatingHours
        if body.mechanicCount is not None:
            garage.mechanic_count = body.mechanicCount
        if body.latitude is not None:
            garage.lat = body.latitude
        if body.longitude is not None:
            garage.lon = body.longitude
        await db.flush()
    else:
        raise HTTPException(status_code=403, detail="Only mechanics and garages can update profiles")

    payload = await user_to_frontend_dict(db, user)
    return {"ok": True, "user": payload}


# ── Availability Toggle ───────────────────────────────
class AvailabilityBody(BaseModel):
    available: bool


@router.patch("/availability")
async def toggle_availability(body: AvailabilityBody, db: DbSession, user: CurrentUser):
    if user.role != UserRole.mechanic.value:
        raise HTTPException(status_code=403, detail="Mechanics only")
    r = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
    mech = r.scalar_one_or_none()
    if not mech:
        raise HTTPException(status_code=404, detail="Mechanic profile not found")
    mech.available = body.available
    await db.flush()
    return {"ok": True, "available": mech.available}


# ── Earnings ──────────────────────────────────────────
@router.get("/earnings")
async def get_earnings(
    db: DbSession,
    user: CurrentUser,
    period: str = Query("week", pattern="^(week|month|all)$"),
):
    if user.role not in (UserRole.mechanic.value, UserRole.garage.value):
        raise HTTPException(status_code=403, detail="Providers only")

    # Determine which jobs belong to this provider
    if user.role == UserRole.mechanic.value:
        mr = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        mech = mr.scalar_one_or_none()
        if not mech:
            return {"earnings": [], "total": 0}
        job_filter = Job.assigned_mechanic_id == mech.id
    else:
        gr = await db.execute(select(Garage).where(Garage.user_id == user.id))
        garage = gr.scalar_one_or_none()
        if not garage:
            return {"earnings": [], "total": 0}
        job_filter = Job.assigned_garage_id == garage.id

    # Date range
    now = datetime.now(timezone.utc)
    if period == "week":
        since = now - timedelta(days=7)
    elif period == "month":
        since = now - timedelta(days=30)
    else:
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)

    # Query payments for completed jobs
    q = (
        select(
            func.date(Payment.created_at).label("day"),
            func.coalesce(func.sum(Payment.amount), 0).label("total"),
        )
        .join(Job, Job.id == Payment.job_id)
        .where(
            job_filter,
            Payment.status == "captured",
            Payment.created_at >= since,
        )
        .group_by(func.date(Payment.created_at))
        .order_by(func.date(Payment.created_at))
    )
    r = await db.execute(q)
    rows = r.mappings().all()

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    earnings = []
    grand_total = 0
    for row in rows:
        day = row["day"]
        total = int(row["total"])
        grand_total += total
        label = day_names[day.weekday()] if hasattr(day, "weekday") else str(day)
        earnings.append({"name": label, "earnings": total // 100, "date": str(day)})

    return {"earnings": earnings, "total": grand_total // 100}
