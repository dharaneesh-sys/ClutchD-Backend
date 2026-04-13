from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.user import User
from app.services import matching
from app.ws.manager import push_location_update, push_status_update

# ---- Status transition guard ------------------------------------------------
VALID_TRANSITIONS: dict[str, set[str]] = {
    "searching": {"assigned", "cancelled"},
    "assigned": {"en_route", "cancelled"},
    "en_route": {"in_progress", "completed", "cancelled"},
    "in_progress": {"payment_pending", "completed", "cancelled"},
    "payment_pending": {"completed", "cancelled"},
    "completed": set(),   # terminal
    "cancelled": set(),   # terminal
}


def _validate_transition(current: str, target: str) -> bool:
    """Return True if *target* is a legal successor of *current*."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def job_response_dict(job: Job, mechanic_summary: dict | None = None) -> dict[str, Any]:
    pricing = None
    if job.total_amount is not None:
        pricing = {
            "serviceAmount": job.service_amount,
            "convenienceFee": job.convenience_fee,
            "cancellationFee": job.cancellation_fee,
            "distanceKm": job.distance_km,
            "distanceFee": job.distance_fee,
            "gstAmount": job.gst_amount,
            "totalAmount": job.total_amount,
            "providerUpiId": job.provider_upi_id,
            "platformUpiId": "amdevanand206@oksbi",
        }
    return {
        "id": str(job.id),
        "issueTag": job.issue_tag,
        "description": job.description,
        "requestType": job.request_type,
        "status": job.status,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "priceEstimate": job.price_estimate,
        "vehicleId": str(job.vehicle_id) if job.vehicle_id else None,
        "mechanic": mechanic_summary,
        "customerLocation": {
            "lat": job.customer_lat,
            "lng": job.customer_lon,
        } if job.customer_lat else None,
        "pricing": pricing,
    }


async def assign_job_auto(db: AsyncSession, job: Job) -> Job:
    """Auto-assign a job using an atomic lock to prevent double-assignment."""
    # Re-fetch with FOR UPDATE so concurrent callers block rather than race
    r = await db.execute(
        select(Job).where(Job.id == job.id, Job.status == "searching").with_for_update()
    )
    locked_job = r.scalar_one_or_none()
    if not locked_job:
        # Another worker already assigned or cancelled this job
        return job

    lat, lon = locked_job.customer_lat, locked_job.customer_lon
    mechs = await matching.nearest_mechanics(db, lat, lon, limit=10, issue_tag=locked_job.issue_tag)
    if not mechs:
        mechs = await matching.nearest_mechanics(db, lat, lon, limit=10, issue_tag=None)
    gar = await matching.nearest_garages(db, lat, lon, limit=10, issue_tag=locked_job.issue_tag)
    if not gar:
        gar = await matching.nearest_garages(db, lat, lon, limit=10, issue_tag=None)

    choice: tuple[str, UUID, dict] | None = None

    if locked_job.request_type == "mechanic" and mechs:
        m = mechs[0]
        choice = ("mechanic", m.id, {"id": str(m.id), "name": m.full_name, "rating": m.rating, "distance": f"{m.distance_m/1000:.1f} km"})
    elif locked_job.request_type == "garage" and gar:
        g = gar[0]
        choice = ("garage", g.id, {"id": str(g.id), "name": g.garage_name, "rating": g.rating, "distance": f"{g.distance_m/1000:.1f} km"})
    elif locked_job.request_type == "auto":
        best_m = mechs[0] if mechs else None
        best_g = gar[0] if gar else None
        if best_m and best_g:
            if best_m.score >= best_g.score:
                choice = (
                    "mechanic",
                    best_m.id,
                    {
                        "id": str(best_m.id),
                        "name": best_m.full_name,
                        "rating": best_m.rating,
                        "distance": f"{best_m.distance_m/1000:.1f} km",
                    },
                )
            else:
                choice = (
                    "garage",
                    best_g.id,
                    {
                        "id": str(best_g.id),
                        "name": best_g.garage_name,
                        "rating": best_g.rating,
                        "distance": f"{best_g.distance_m/1000:.1f} km",
                    },
                )
        elif best_m:
            choice = (
                "mechanic",
                best_m.id,
                {
                    "id": str(best_m.id),
                    "name": best_m.full_name,
                    "rating": best_m.rating,
                    "distance": f"{best_m.distance_m/1000:.1f} km",
                },
            )
        elif best_g:
            choice = (
                "garage",
                best_g.id,
                {
                    "id": str(best_g.id),
                    "name": best_g.garage_name,
                    "rating": best_g.rating,
                    "distance": f"{best_g.distance_m/1000:.1f} km",
                },
            )

    if choice:
        atype, eid, summary = choice
        locked_job.assigned_type = atype
        locked_job.status = "assigned"
        if atype == "mechanic":
            locked_job.assigned_mechanic_id = eid
            locked_job.assigned_garage_id = None
            r = await db.execute(select(Mechanic).where(Mechanic.id == eid))
            m = r.scalar_one_or_none()
            if m:
                await push_location_update(str(locked_job.user_id), str(locked_job.id), [m.lat, m.lon])
        else:
            locked_job.assigned_garage_id = eid
            locked_job.assigned_mechanic_id = None
            r = await db.execute(select(Garage).where(Garage.id == eid))
            g = r.scalar_one_or_none()
            if g:
                await push_location_update(str(locked_job.user_id), str(locked_job.id), [g.lat, g.lon])
        await push_status_update(str(locked_job.user_id), str(locked_job.id), "assigned", summary)
    else:
        locked_job.status = "searching"

    await db.flush()
    # Sync the original reference so callers see updated values
    job.status = locked_job.status
    job.assigned_type = locked_job.assigned_type
    job.assigned_mechanic_id = locked_job.assigned_mechanic_id
    job.assigned_garage_id = locked_job.assigned_garage_id
    return job


def assignee_summary(job: Job, db_row: Mechanic | Garage | None) -> dict | None:
    if not db_row:
        return None
    
    dist_str = "Unknown distance"
    if db_row.lat is not None and db_row.lon is not None:
        try:
            dist = matching.haversine_m(job.customer_lat, job.customer_lon, db_row.lat, db_row.lon)
            dist_str = f"{dist/1000:.1f} km"
        except Exception:
            pass

    if isinstance(db_row, Mechanic):
        return {
            "id": str(db_row.id),
            "name": db_row.full_name,
            "rating": db_row.rating,
            "distance": dist_str,
        }
    return {
        "id": str(db_row.id),
        "name": db_row.garage_name,
        "rating": db_row.rating,
        "distance": dist_str,
    }


async def create_service_request(db: AsyncSession, user: User, data: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    lat = data.get("customerLat")
    lon = data.get("customerLng")
    if lat is None:
        lat = settings.default_map_lat
    if lon is None:
        lon = settings.default_map_lon

    job = Job(
        user_id=user.id,
        issue_tag=data["issueTag"],
        description=data["description"],
        request_type=data.get("requestType", "auto"),
        status="searching",
        customer_lat=float(lat),
        customer_lon=float(lon),
        price_estimate=data.get("priceEstimate"),
        media_url=data.get("mediaUrl"),
        vehicle_id=data.get("vehicleId"),
    )
    db.add(job)
    await db.flush()
    await assign_job_auto(db, job)

    if job.status == "searching":
        try:
            from app.tasks.worker import retry_job_assignment

            retry_job_assignment.apply_async(args=[str(job.id)], countdown=45)
        except Exception:
            pass

    mech_summary = None
    if job.status == "assigned" and job.assigned_mechanic_id:
        r = await db.execute(select(Mechanic).where(Mechanic.id == job.assigned_mechanic_id))
        mech_summary = assignee_summary(job, r.scalar_one_or_none())
    elif job.status == "assigned" and job.assigned_garage_id:
        r = await db.execute(select(Garage).where(Garage.id == job.assigned_garage_id))
        mech_summary = assignee_summary(job, r.scalar_one_or_none())

    return job_response_dict(job, mech_summary)


async def get_job_for_user(db: AsyncSession, job_id: UUID, user: User) -> Job | None:
    r = await db.execute(select(Job).where(Job.id == job_id))
    job = r.scalar_one_or_none()
    if not job:
        return None
    if user.role == "admin" or user.is_superuser:
        return job
    if job.user_id == user.id:
        return job
    if user.role == "mechanic":
        m = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        mech = m.scalar_one_or_none()
        if mech and job.assigned_mechanic_id == mech.id:
            return job
    if user.role == "garage":
        g = await db.execute(select(Garage).where(Garage.user_id == user.id))
        gar = g.scalar_one_or_none()
        if gar and job.assigned_garage_id == gar.id:
            return job
    return None


class InvalidTransitionError(Exception):
    """Raised when a status transition is not allowed."""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from '{current}' to '{target}'")


async def patch_job_status(
    db: AsyncSession,
    job: Job,
    status: str,
    mechanic_id: UUID | None,
) -> dict[str, Any]:
    if not _validate_transition(job.status, status):
        raise InvalidTransitionError(job.status, status)

    job.status = status
    if mechanic_id and job.assigned_mechanic_id != mechanic_id:
        job.assigned_mechanic_id = mechanic_id
        job.assigned_type = "mechanic"
    await db.flush()
    mech_summary = None
    if job.assigned_mechanic_id:
        r = await db.execute(select(Mechanic).where(Mechanic.id == job.assigned_mechanic_id))
        mech_summary = assignee_summary(job, r.scalar_one_or_none())
    await push_status_update(str(job.user_id), str(job.id), status, mech_summary)
    
    from app.models.notification import Notification
    titles = {
        "assigned": "Job Assigned",
        "en_route": "Mechanic En Route",
        "in_progress": "Job In Progress",
        "completed": "Job Completed",
        "cancelled": "Job Cancelled"
    }
    bodies = {
        "assigned": "A mechanic has been assigned to your service request.",
        "en_route": "The mechanic is on their way to your location.",
        "in_progress": "The mechanic has started working on your vehicle.",
        "completed": "Your service request is complete.",
        "cancelled": "Your service request was cancelled."
    }
    if status in titles:
        notif = Notification(
            user_id=job.user_id,
            title=titles[status],
            body=bodies[status],
            type="job_update",
            job_id=job.id
        )
        db.add(notif)
        await db.flush()
        
        # update unread count
        from sqlalchemy import func
        c = await db.execute(select(func.count(Notification.id)).where(Notification.user_id == job.user_id, Notification.read == False))
        from app.ws.manager import manager
        await manager.send_json_to_user(str(job.user_id), {"type": "NOTIFICATION_UPDATE", "payload": {"unreadCount": c.scalar() or 0}})
    
    if job.assigned_mechanic_id:
        r = await db.execute(select(Mechanic).where(Mechanic.id == job.assigned_mechanic_id))
        m = r.scalar_one_or_none()
        if m:
            await push_location_update(str(job.user_id), str(job.id), [m.lat, m.lon])
    return job_response_dict(job, mech_summary)


# ---- Fee constants -------------------------------------------------------
CONVENIENCE_FEE = 40.0
CANCELLATION_FEE = 30.0
DAY_RATE_PER_KM = 1.0
NIGHT_RATE_PER_KM = 2.0  # after 8 PM
GST_RATE = 0.18
PLATFORM_UPI = "amdevanand206@oksbi"


async def finalize_job_price(
    db: AsyncSession,
    job: Job,
    service_amount: float,
) -> dict[str, Any]:
    """Called by mechanic/garage after completing the work.
    Calculates all fees, sets status to payment_pending, and returns the
    full pricing breakdown.
    """
    from datetime import datetime, timezone

    if job.status not in ("in_progress", "en_route"):
        raise InvalidTransitionError(job.status, "payment_pending")

    # ---- Distance calculation -----------------------------------------
    provider_lat: float | None = None
    provider_lon: float | None = None

    if job.assigned_mechanic_id:
        r = await db.execute(select(Mechanic).where(Mechanic.id == job.assigned_mechanic_id))
        provider = r.scalar_one_or_none()
        if provider:
            provider_lat, provider_lon = provider.lat, provider.lon
            job.provider_upi_id = provider.upi_id
    elif job.assigned_garage_id:
        r = await db.execute(select(Garage).where(Garage.id == job.assigned_garage_id))
        provider = r.scalar_one_or_none()
        if provider:
            provider_lat, provider_lon = provider.lat, provider.lon
            job.provider_upi_id = provider.upi_id

    distance_km = 0.0
    if provider_lat is not None and job.customer_lat is not None:
        distance_m = matching.haversine_m(job.customer_lat, job.customer_lon, provider_lat, provider_lon)
        distance_km = round(distance_m / 1000, 2)

    # ---- Night-time check (after 8 PM IST) ----------------------------
    from datetime import timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    is_night = now_ist.hour >= 20 or now_ist.hour < 6
    rate_per_km = NIGHT_RATE_PER_KM if is_night else DAY_RATE_PER_KM
    distance_fee = round(distance_km * rate_per_km, 2)

    # ---- Calculate totals ---------------------------------------------
    subtotal = service_amount + CONVENIENCE_FEE + CANCELLATION_FEE + distance_fee
    gst_amount = round(subtotal * GST_RATE, 2)
    total_amount = round(subtotal + gst_amount, 2)

    # ---- Persist -------------------------------------------------------
    job.service_amount = service_amount
    job.convenience_fee = CONVENIENCE_FEE
    job.cancellation_fee = CANCELLATION_FEE
    job.distance_km = distance_km
    job.distance_fee = distance_fee
    job.gst_amount = gst_amount
    job.total_amount = total_amount
    job.status = "payment_pending"
    await db.flush()

    # Push update to customer
    mech_summary = None
    if job.assigned_mechanic_id:
        r = await db.execute(select(Mechanic).where(Mechanic.id == job.assigned_mechanic_id))
        mech_summary = assignee_summary(job, r.scalar_one_or_none())
    elif job.assigned_garage_id:
        r = await db.execute(select(Garage).where(Garage.id == job.assigned_garage_id))
        mech_summary = assignee_summary(job, r.scalar_one_or_none())

    pricing_ws = {
        "serviceAmount": service_amount,
        "convenienceFee": CONVENIENCE_FEE,
        "cancellationFee": CANCELLATION_FEE,
        "distanceKm": distance_km,
        "distanceFee": distance_fee,
        "gstAmount": gst_amount,
        "totalAmount": total_amount,
        "providerUpiId": job.provider_upi_id,
        "platformUpiId": PLATFORM_UPI,
    }
    await push_status_update(str(job.user_id), str(job.id), "payment_pending", mech_summary, pricing_ws)

    # Notification for customer
    from app.models.notification import Notification
    notif = Notification(
        user_id=job.user_id,
        title="Invoice Ready",
        body=f"Your service bill of \u20b9{total_amount:.0f} is ready for payment.",
        type="job_update",
        job_id=job.id,
    )
    db.add(notif)
    await db.flush()

    from sqlalchemy import func as sa_func
    from app.models.notification import Notification as N
    c = await db.execute(select(sa_func.count(N.id)).where(N.user_id == job.user_id, N.read == False))
    from app.ws.manager import manager
    await manager.send_json_to_user(str(job.user_id), {"type": "NOTIFICATION_UPDATE", "payload": {"unreadCount": c.scalar() or 0}})

    return job_response_dict(job, mech_summary)


async def complete_job_with_payment(
    db: AsyncSession,
    job: Job,
    payment: dict[str, Any],
) -> dict[str, Any]:
    # We no longer insert duplicate 'manual' Payment records here since 
    # payments.py explicitly records 'razorpay', 'cash', and 'qr' records natively.
    if job.status == "cancelled":
        raise InvalidTransitionError("cancelled", "completed")

    if job.status != "completed":
        job.status = "completed"
        await db.flush()
        await push_status_update(str(job.user_id), str(job.id), "completed", None)

    return job_response_dict(job, None)


async def cancel_job(db: AsyncSession, job: Job) -> None:
    if job.status in ("completed", "cancelled"):
        return  # Idempotent — already in terminal state
    job.status = "cancelled"
    await db.flush()
    await push_status_update(str(job.user_id), str(job.id), "cancelled", None)
