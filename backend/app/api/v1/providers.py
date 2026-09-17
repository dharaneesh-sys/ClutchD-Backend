from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.enums import UserRole
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.payment import Payment
from app.models.provider_offer import ProviderOffer
from app.services import matching
from app.services.user_payload import user_to_frontend_dict

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/nearby")
@limiter.limit("30/minute")
async def nearby_providers(
    request: Request,
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


# ── Location Check-in ─────────────────────────────────
class LocationUpdateBody(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


@router.put("/location")
@limiter.limit("30/minute")
async def update_provider_location(
    request: Request,
    body: LocationUpdateBody,
    db: DbSession,
    user: CurrentUser,
):
    """Providers check in their current GPS position here on every login.

    The nearby search (GET /providers/nearby) ranks by these stored
    coordinates, so customers discover providers at their real current
    location — not wherever they signed up from.
    """
    if user.role == UserRole.mechanic.value:
        r = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        mech = r.scalar_one_or_none()
        if not mech:
            raise HTTPException(status_code=404, detail="Mechanic profile not found")
        mech.lat = body.latitude
        mech.lon = body.longitude
        # Capture before flush — post-flush attribute access on an updated row
        # can trigger a lazy refresh (MissingGreenlet) inside the async session.
        lat, lon = mech.lat, mech.lon
        await db.flush()
        # Live tracking: push the new position to the customer of every active
        # job (assigned/en_route/in_progress) so their map marker and ETA
        # update while the mechanic is on the road, not just at state changes.
        from app.models.job import Job
        from app.ws.manager import push_location_update

        r2 = await db.execute(
            select(Job).where(
                Job.assigned_mechanic_id == mech.id,
                Job.status.in_(["assigned", "en_route", "in_progress"]),
            )
        )
        for active_job in r2.scalars():
            await push_location_update(str(active_job.user_id), str(active_job.id), [lat, lon])
        return {"ok": True, "role": "mechanic", "latitude": lat, "longitude": lon}

    if user.role == UserRole.garage.value:
        r = await db.execute(select(Garage).where(Garage.user_id == user.id))
        garage = r.scalar_one_or_none()
        if not garage:
            raise HTTPException(status_code=404, detail="Garage profile not found")
        garage.lat = body.latitude
        garage.lon = body.longitude
        lat, lon = garage.lat, garage.lon
        await db.flush()
        # Live tracking for garages, same as mechanics above.
        from app.models.job import Job
        from app.ws.manager import push_location_update

        r2 = await db.execute(
            select(Job).where(
                Job.assigned_garage_id == garage.id,
                Job.status.in_(["assigned", "en_route", "in_progress"]),
            )
        )
        for active_job in r2.scalars():
            await push_location_update(str(active_job.user_id), str(active_job.id), [lat, lon])
        return {"ok": True, "role": "garage", "latitude": lat, "longitude": lon}

    raise HTTPException(status_code=403, detail="Only mechanics and garages can check in a provider location")


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
    upiId: str | None = Field(None, max_length=128, pattern=r"^[\w\.\-_]+@[\w\.\-_]+$")


@router.patch("/profile")
@limiter.limit("10/minute")
async def update_profile(request: Request, body: ProfileUpdateBody, db: DbSession, user: CurrentUser):
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
        if body.upiId is not None:
            mech.upi_id = body.upiId
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
        if body.upiId is not None:
            garage.upi_id = body.upiId
        await db.flush()
    else:
        raise HTTPException(status_code=403, detail="Only mechanics and garages can update profiles")

    payload = await user_to_frontend_dict(db, user)
    return {"ok": True, "user": payload}


# ── Availability Toggle ───────────────────────────────
class AvailabilityBody(BaseModel):
    available: bool


@router.patch("/availability")
@limiter.limit("10/minute")
async def toggle_availability(request: Request, body: AvailabilityBody, db: DbSession, user: CurrentUser):
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


# ── Provider Offers ────────────────────────────────────
@router.get("/offers")
@limiter.limit("30/minute")
async def get_provider_offers(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, pattern="^(pending|expired)$"),
):
    if user.role not in (UserRole.mechanic.value, UserRole.garage.value):
        raise HTTPException(status_code=403, detail="Providers only")

    if user.role == UserRole.mechanic.value:
        mr = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        provider = mr.scalar_one_or_none()
    else:
        gr = await db.execute(select(Garage).where(Garage.user_id == user.id))
        provider = gr.scalar_one_or_none()

    if not provider:
        return {"offers": []}

    query = (
        select(ProviderOffer, Job)
        .join(Job, Job.id == ProviderOffer.job_id)
        .where(
            ProviderOffer.provider_type == user.role,
            ProviderOffer.provider_id == provider.id,
        )
    )

    if status == "pending":
        query = query.where(ProviderOffer.status == "pending")
    elif status == "expired":
        query = query.where(ProviderOffer.expires_at <= func.now())
    else:
        query = query.where(ProviderOffer.expires_at > func.now())

    query = query.order_by(ProviderOffer.created_at.desc()).offset(offset).limit(limit)

    r = await db.execute(query)
    rows = r.all()

    offers_list = []
    for po, j in rows:
        offers_list.append({
            "id": str(po.id),
            "jobId": str(po.job_id),
            "providerType": po.provider_type,
            "providerId": str(po.provider_id),
            "status": po.status,
            "expiresAt": po.expires_at.isoformat() if po.expires_at else None,
            "createdAt": po.created_at.isoformat() if po.created_at else None,
            "job": {
                "issueTag": j.issue_tag,
                "description": j.description,
                "customerLocation": {"lat": j.customer_lat, "lng": j.customer_lon} if j.customer_lat else None,
                "status": j.status,
            },
        })
    return {"offers": offers_list, "total": len(offers_list)}
