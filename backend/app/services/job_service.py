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


def job_response_dict(job: Job, mechanic_summary: dict | None = None) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "issueTag": job.issue_tag,
        "description": job.description,
        "requestType": job.request_type,
        "status": job.status,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "priceEstimate": job.price_estimate,
        "mechanic": mechanic_summary,
    }


async def assign_job_auto(db: AsyncSession, job: Job) -> Job:
    lat, lon = job.customer_lat, job.customer_lon
    mechs = await matching.nearest_mechanics(db, lat, lon, limit=10, issue_tag=job.issue_tag)
    if not mechs:
        mechs = await matching.nearest_mechanics(db, lat, lon, limit=10, issue_tag=None)
    gar = await matching.nearest_garages(db, lat, lon, limit=10, issue_tag=job.issue_tag)
    if not gar:
        gar = await matching.nearest_garages(db, lat, lon, limit=10, issue_tag=None)

    choice: tuple[str, UUID, dict] | None = None

    if job.request_type == "mechanic" and mechs:
        m = mechs[0]
        choice = ("mechanic", m.id, {"id": str(m.id), "name": m.full_name, "rating": m.rating, "distance": f"{m.distance_m/1000:.1f} km"})
    elif job.request_type == "garage" and gar:
        g = gar[0]
        choice = ("garage", g.id, {"id": str(g.id), "name": g.garage_name, "rating": g.rating, "distance": f"{g.distance_m/1000:.1f} km"})
    elif job.request_type == "auto":
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
        job.assigned_type = atype
        job.status = "assigned"
        if atype == "mechanic":
            job.assigned_mechanic_id = eid
            job.assigned_garage_id = None
            r = await db.execute(select(Mechanic).where(Mechanic.id == eid))
            m = r.scalar_one_or_none()
            if m:
                await push_location_update(str(job.user_id), str(job.id), [m.lat, m.lon])
        else:
            job.assigned_garage_id = eid
            job.assigned_mechanic_id = None
            r = await db.execute(select(Garage).where(Garage.id == eid))
            g = r.scalar_one_or_none()
            if g:
                await push_location_update(str(job.user_id), str(job.id), [g.lat, g.lon])
        await push_status_update(str(job.user_id), str(job.id), "assigned", summary)
    else:
        job.status = "searching"

    await db.flush()
    return job


def assignee_summary(job: Job, db_row: Mechanic | Garage | None) -> dict | None:
    if not db_row:
        return None
    if isinstance(db_row, Mechanic):
        dist = matching.haversine_m(job.customer_lat, job.customer_lon, db_row.lat, db_row.lon)
        return {
            "id": str(db_row.id),
            "name": db_row.full_name,
            "rating": db_row.rating,
            "distance": f"{dist/1000:.1f} km",
        }
    dist = matching.haversine_m(job.customer_lat, job.customer_lon, db_row.lat, db_row.lon)
    return {
        "id": str(db_row.id),
        "name": db_row.garage_name,
        "rating": db_row.rating,
        "distance": f"{dist/1000:.1f} km",
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


async def patch_job_status(
    db: AsyncSession,
    job: Job,
    status: str,
    mechanic_id: UUID | None,
) -> dict[str, Any]:
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
    if job.assigned_mechanic_id:
        r = await db.execute(select(Mechanic).where(Mechanic.id == job.assigned_mechanic_id))
        m = r.scalar_one_or_none()
        if m:
            await push_location_update(str(job.user_id), str(job.id), [m.lat, m.lon])
    return job_response_dict(job, mech_summary)


async def complete_job_with_payment(
    db: AsyncSession,
    job: Job,
    payment: dict[str, Any],
) -> dict[str, Any]:
    from app.models.payment import Payment

    job.status = "completed"
    amt = payment.get("amount") or (job.price_estimate or {}).get("min") or 0
    pay = Payment(
        job_id=job.id,
        provider="manual",
        external_id=payment.get("transactionId"),
        amount=float(amt),
        status="succeeded" if payment.get("status") == "success" else "pending",
        raw_payload=payment,
    )
    db.add(pay)
    await db.flush()
    await push_status_update(str(job.user_id), str(job.id), "completed", None)
    return job_response_dict(job, None)


async def cancel_job(db: AsyncSession, job: Job) -> None:
    job.status = "cancelled"
    await db.flush()
    await push_status_update(str(job.user_id), str(job.id), "cancelled", None)
