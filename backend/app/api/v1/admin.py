from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.audit import audit_log
from app.api.deps import DbSession, require_admin
from app.models.dispute import Dispute
from app.models.enums import DisputeStatus, UserRole
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Type alias for admin-protected routes ──────────────────────
AdminUser = Annotated[User, Depends(require_admin)]


def _user_display_name(db_user: User) -> str:
    """Resolve a user's display name from their profile."""
    if db_user.role == UserRole.mechanic.value and db_user.mechanic_profile:
        return db_user.mechanic_profile.full_name
    if db_user.role == UserRole.garage.value and db_user.garage_profile:
        return db_user.garage_profile.garage_name
    if db_user.role == UserRole.customer.value:
        return db_user.email.split("@")[0]
    return db_user.email


# ── Users ─────────────────────────────────────────────────────
@router.get("/users")
async def list_users(
    db: DbSession,
    user: AdminUser,
    role: str | None = Query(None, pattern="^(customer|mechanic|garage|admin)$"),
    status_filter: str | None = Query(None, alias="status", pattern="^(active|suspended|pending)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    q = select(User).options(joinedload(User.mechanic_profile), joinedload(User.garage_profile))
    if role:
        q = q.where(User.role == role)
    if status_filter == "active":
        q = q.where(User.is_active == True)
    elif status_filter == "suspended":
        q = q.where(User.is_active == False)
    elif status_filter == "pending":
        q = q.where(User.role.in_(["mechanic", "garage"]), User.is_active == True)
        # Sub-filter: mechanics/garages where not verified
        # We handle this in the response loop instead for simplicity
    q = q.offset(skip).limit(limit).order_by(User.created_at.desc())
    result = await db.execute(q)
    users = result.unique().scalars().all()

    result_list = []
    for u in users:
        name = _user_display_name(u)
        status_val = "Active" if u.is_active else "Suspended"
        if u.role in ("mechanic", "garage") and u.is_active:
            profile = u.mechanic_profile or u.garage_profile
            if profile and not profile.verified:
                status_val = "Pending"
        result_list.append({
            "id": str(u.id),
            "name": name,
            "email": u.email,
            "role": u.role.capitalize(),
            "status": status_val,
            "joined": u.created_at.strftime("%d %b %Y") if u.created_at else "—",
        })

    return {"users": result_list}


# ── User status toggle (Suspend / Activate) ───────────────────
class UserStatusBody(BaseModel):
    is_active: bool


@router.patch("/users/{user_id}/status")
@audit_log("toggle_user_status", "user", entity_id_arg="user_id")
async def toggle_user_status(
    user_id: UUID,
    body: UserStatusBody,
    db: DbSession,
    user: AdminUser,
):
    result = await db.execute(select(User).where(User.id == user_id))
    u = result.scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if u.role == UserRole.admin.value:
        raise HTTPException(status_code=403, detail="Cannot suspend another admin")
    u.is_active = body.is_active
    await db.flush()
    return {"ok": True, "isActive": u.is_active}


# ── Verification ──────────────────────────────────────────────
class VerifyBody(BaseModel):
    verified: bool = True


@router.patch("/mechanic/{mechanic_id}/verify")
@audit_log("verify_mechanic", "mechanic", entity_id_arg="mechanic_id")
async def verify_mechanic(mechanic_id: UUID, body: VerifyBody, db: DbSession, user: AdminUser):
    result = await db.execute(select(Mechanic).where(Mechanic.id == mechanic_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Mechanic not found")
    m.verified = body.verified
    await db.flush()
    return {"ok": True, "verified": m.verified}


@router.patch("/garage/{garage_id}/verify")
@audit_log("verify_garage", "garage", entity_id_arg="garage_id")
async def verify_garage(garage_id: UUID, body: VerifyBody, db: DbSession, user: AdminUser):
    result = await db.execute(select(Garage).where(Garage.id == garage_id))
    g = result.scalar_one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Garage not found")
    g.verified = body.verified
    await db.flush()
    return {"ok": True, "verified": g.verified}


# ── Pending KYC ───────────────────────────────────────────────
@router.get("/kyc/pending")
async def list_pending_kyc(db: DbSession, user: AdminUser):
    # Unverified mechanics
    mech_q = (
        select(Mechanic)
        .options(joinedload(Mechanic.user))
        .where(Mechanic.verified == False)
    )
    mech_result = await db.execute(mech_q)
    mechanics = mech_result.unique().scalars().all()

    # Unverified garages
    garage_q = (
        select(Garage)
        .options(joinedload(Garage.user))
        .where(Garage.verified == False)
    )
    garage_result = await db.execute(garage_q)
    garages = garage_result.unique().scalars().all()

    applications = []
    for m in mechanics:
        applications.append({
            "id": str(m.id),
            "name": m.full_name,
            "type": "Independent Mechanic",
            "profileType": "mechanic",
            "submitted": m.created_at.strftime("%d %b %Y %I:%M %p") if m.created_at else "—",
            "status": "Pending",
            "documents": ["Aadhaar", "Driving License", "Skill Certificate"],
        })
    for g in garages:
        applications.append({
            "id": str(g.id),
            "name": g.garage_name,
            "type": "Garage Enterprise",
            "profileType": "garage",
            "submitted": g.created_at.strftime("%d %b %Y %I:%M %p") if g.created_at else "—",
            "status": "Pending",
            "documents": ["GST Registration", "Shop Establishment Act", "Owner ID"],
        })

    # Sort by submitted descending
    applications.sort(key=lambda a: a["submitted"], reverse=True)

    return {"applications": applications}


# ── Analytics ─────────────────────────────────────────────────
@router.get("/analytics")
async def analytics(db: DbSession, user: AdminUser):
    from sqlalchemy import text
    row = (await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM jobs WHERE status = 'completed') AS jobs_completed,
            (SELECT COUNT(*) FROM jobs) AS total_jobs,
            (SELECT COALESCE(SUM(amount), 0) FROM payments) AS total_revenue,
            (SELECT COUNT(*) FROM mechanic) AS total_mechanics,
            (SELECT COUNT(*) FROM garage) AS total_garages
    """))).one()
    return {
        "totalUsers": row.total_users,
        "totalJobs": row.total_jobs,
        "jobsCompleted": row.jobs_completed,
        "activeProviders": (row.total_mechanics or 0) + (row.total_garages or 0),
        "totalMechanics": row.total_mechanics,
        "totalGarages": row.total_garages,
        "totalRevenue": row.total_revenue,
    }


# ── Mechanics List ────────────────────────────────────────────
@router.get("/mechanics")
async def list_mechanics(db: DbSession, user: AdminUser):
    q = (
        select(Mechanic)
        .options(joinedload(Mechanic.user))
        .order_by(Mechanic.created_at.desc())
    )
    result = await db.execute(q)
    mechanics = result.unique().scalars().all()

    # Count completed jobs per mechanic
    mechanic_ids = [m.id for m in mechanics]
    job_counts = {}
    if mechanic_ids:
        from sqlalchemy import func as safunc
        count_q = (
            select(Job.assigned_mechanic_id, safunc.count(Job.id))
            .where(Job.assigned_mechanic_id.in_(mechanic_ids), Job.status == "completed")
            .group_by(Job.assigned_mechanic_id)
        )
        count_result = await db.execute(count_q)
        for mid, cnt in count_result:
            job_counts[str(mid)] = cnt

    result_list = []
    for m in mechanics:
        jobs_done = job_counts.get(str(m.id), 0)
        result_list.append({
            "id": str(m.id),
            "userId": str(m.user_id),
            "fullName": m.full_name,
            "phone": m.phone,
            "experience": m.experience,
            "expertise": m.expertise or [],
            "location": m.location_address,
            "lat": m.lat,
            "lon": m.lon,
            "rating": m.rating,
            "verified": m.verified,
            "available": m.available,
            "penalized": m.penalized,
            "penaltyAmount": m.penalty_amount,
            "jobsCompleted": jobs_done,
            "createdAt": m.created_at.isoformat() if m.created_at else None,
        })

    return {"mechanics": result_list}


# ── Garages List ──────────────────────────────────────────────
@router.get("/garages")
async def list_garages(db: DbSession, user: AdminUser):
    q = (
        select(Garage)
        .options(joinedload(Garage.user))
        .order_by(Garage.created_at.desc())
    )
    result = await db.execute(q)
    garages = result.unique().scalars().all()

    garage_ids = [g.id for g in garages]
    job_counts = {}
    if garage_ids:
        from sqlalchemy import func as safunc
        count_q = (
            select(Job.assigned_garage_id, safunc.count(Job.id))
            .where(Job.assigned_garage_id.in_(garage_ids), Job.status == "completed")
            .group_by(Job.assigned_garage_id)
        )
        count_result = await db.execute(count_q)
        for gid, cnt in count_result:
            job_counts[str(gid)] = cnt

    result_list = []
    for g in garages:
        jobs_done = job_counts.get(str(g.id), 0)
        result_list.append({
            "id": str(g.id),
            "userId": str(g.user_id),
            "garageName": g.garage_name,
            "ownerName": g.owner_name,
            "phone": g.phone,
            "services": g.services or [],
            "mechanicCount": g.mechanic_count,
            "operatingHours": g.operating_hours,
            "location": g.location_address,
            "lat": g.lat,
            "lon": g.lon,
            "rating": g.rating,
            "verified": g.verified,
            "penalized": g.penalized,
            "penaltyAmount": g.penalty_amount,
            "jobsCompleted": jobs_done,
            "createdAt": g.created_at.isoformat() if g.created_at else None,
        })

    return {"garages": result_list}


# ── Payments List ─────────────────────────────────────────────
@router.get("/payments")
async def list_payments(
    db: DbSession,
    user: AdminUser,
    status_filter: str | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    q = select(Payment).options(
        joinedload(Payment.job).joinedload(Job.customer),
    ).order_by(Payment.created_at.desc())

    if status_filter:
        q = q.where(Payment.status == status_filter)

    q = q.offset(skip).limit(limit)
    result = await db.execute(q)
    payments = result.unique().scalars().all()

    result_list = []
    for p in payments:
        user_name = "Unknown"
        if p.job and p.job.customer:
            user_name = _user_display_name(p.job.customer)

        result_list.append({
            "id": str(p.id),
            "jobId": str(p.job_id) if p.job_id else None,
            "userId": str(p.user_id) if p.user_id else None,
            "userName": user_name,
            "amount": p.amount,
            "currency": p.currency.upper() if p.currency else "INR",
            "formattedAmount": f"₹{p.amount / 100:,.0f}" if p.amount else "—",
            "provider": p.provider,
            "status": p.status.capitalize() if p.status else "Pending",
            "method": p.method.upper() if p.method else "—",
            "createdAt": p.created_at.isoformat() if p.created_at else None,
            "updatedAt": p.updated_at.isoformat() if p.updated_at else None,
        })

    return {"payments": result_list}


# ── Payment Refund ────────────────────────────────────────────
class PaymentRefundBody(BaseModel):
    amount: int = Field(ge=0, le=50000000, description="Amount in paise")
    notes: str | None = Field(None, max_length=1000)


@router.post("/payments/{payment_id}/refund")
@audit_log("refund_payment", "payment", entity_id_arg="payment_id")
async def refund_payment(
    payment_id: UUID,
    body: PaymentRefundBody,
    db: DbSession,
    user: AdminUser,
):
    result = await db.execute(
        select(Payment).options(joinedload(Payment.job)).where(Payment.id == payment_id)
    )
    p = result.unique().scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    if p.status != "captured":
        raise HTTPException(status_code=400, detail="Payment is not in captured state")

    p.status = "refunded"
    p.updated_at = datetime.now(timezone.utc)

    if p.job:
        db.add(Notification(
            user_id=p.job.user_id,
            title="Payment Refunded",
            body=f"A refund of ₹{body.amount / 100:,.0f} has been processed for payment {p.id}.",
            type="system",
            job_id=p.job_id,
        ))

    await db.flush()
    return {"ok": True, "message": f"Refund of ₹{body.amount / 100:,.0f} processed."}


# ── Admin Jobs ────────────────────────────────────────────────
ISSUE_LABELS = {
    "flat_tire": "Flat Tire", "engine_failure": "Engine Failure",
    "battery_dead": "Dead Battery", "overheating": "Overheating",
    "brake_issue": "Brake Issue", "oil_leak": "Oil Leak",
    "electrical": "Electrical Problem", "ac_not_working": "AC Not Working",
    "transmission": "Transmission Issue", "starting_issue": "Won't Start",
    "noise": "Strange Noise", "other": "Other",
}

JOB_STATUS_LABELS = {
    "searching": "Searching", "assigned": "Assigned", "en_route": "En Route",
    "in_progress": "In Progress", "completed": "Completed", "cancelled": "Cancelled",
    "idle": "Idle",
}


@router.get("/jobs")
async def list_jobs(
    db: DbSession,
    user: AdminUser,
    status_filter: str | None = Query(None, alias="status"),
):
    q = (
        select(Job)
        .options(
            joinedload(Job.customer),
            joinedload(Job.assigned_mechanic),
            joinedload(Job.assigned_garage),
        )
    )
    if status_filter:
        q = q.where(Job.status == status_filter)
    q = q.order_by(Job.created_at.desc())
    result = await db.execute(q)
    jobs = result.unique().scalars().all()

    result_list = []
    for j in jobs:
        customer_name = _user_display_name(j.customer) if j.customer else "Unknown"
        provider_name = "Unassigned"
        if j.assigned_mechanic:
            provider_name = j.assigned_mechanic.full_name
        elif j.assigned_garage:
            provider_name = j.assigned_garage.garage_name

        amount = "—"
        if j.total_amount is not None:
            amount = f"₹{j.total_amount:,.0f}"
        elif j.price is not None:
            amount = f"₹{j.price:,.0f}"

        time_ago = "—"
        if j.created_at:
            delta = datetime.now(timezone.utc) - j.created_at
            mins = int(delta.total_seconds() / 60)
            if mins < 60:
                time_ago = f"{mins} mins ago"
            else:
                hours = mins // 60
                time_ago = f"{hours}h ago"

        issue_label = ISSUE_LABELS.get(j.issue_tag, j.issue_tag)
        status_label = JOB_STATUS_LABELS.get(j.status, j.status.capitalize())

        result_list.append({
            "id": str(j.id),
            "customer": customer_name,
            "provider": provider_name,
            "status": status_label,
            "location": j.customer_lat and j.customer_lon
                and f"{j.customer_lat:.4f}, {j.customer_lon:.4f}" or "—",
            "amount": amount,
            "issue": issue_label,
            "time": time_ago,
            "customerLat": j.customer_lat,
            "customerLon": j.customer_lon,
        })

    return {"jobs": result_list}


# ── Force Assign ──────────────────────────────────────────────
class ForceAssignBody(BaseModel):
    provider_type: str = Field(pattern="^(mechanic|garage)$")
    provider_id: UUID


@router.post("/jobs/{job_id}/force-assign")
@audit_log("force_assign_job", "job", entity_id_arg="job_id")
async def force_assign_job(
    job_id: UUID,
    body: ForceAssignBody,
    db: DbSession,
    user: AdminUser,
):
    result = await db.execute(select(Job).where(Job.id == job_id))
    j = result.scalar_one_or_none()
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")

    if body.provider_type == "mechanic":
        mech_result = await db.execute(select(Mechanic).where(Mechanic.id == body.provider_id))
        m = mech_result.scalar_one_or_none()
        if not m:
            raise HTTPException(status_code=404, detail="Mechanic not found")
        j.assigned_mechanic_id = m.id
        j.assigned_type = "mechanic"
        j.status = "assigned"
        # Notify mechanic
        db.add(Notification(
            user_id=m.user_id,
            title="Job Force-Assigned",
            body=f"Admin has assigned job {j.id} to you.",
            type="job_update",
            job_id=j.id,
        ))
    else:
        garage_result = await db.execute(select(Garage).where(Garage.id == body.provider_id))
        g = garage_result.scalar_one_or_none()
        if not g:
            raise HTTPException(status_code=404, detail="Garage not found")
        j.assigned_garage_id = g.id
        j.assigned_type = "garage"
        j.status = "assigned"
        db.add(Notification(
            user_id=g.user_id,
            title="Job Force-Assigned",
            body=f"Admin has assigned job {j.id} to your garage.",
            type="job_update",
            job_id=j.id,
        ))

    await db.flush()
    return {"ok": True, "jobId": str(j.id), "status": j.status}


# ── Track Job Map Data ───────────────────────────────────────
@router.get("/jobs/{job_id}/track")
async def track_job(
    job_id: UUID,
    db: DbSession,
    user: AdminUser,
):
    result = await db.execute(
        select(Job)
        .options(
            joinedload(Job.assigned_mechanic),
            joinedload(Job.assigned_garage),
        )
        .where(Job.id == job_id)
    )
    j = result.unique().scalar_one_or_none()
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")

    provider_lat = None
    provider_lon = None
    if j.assigned_mechanic:
        provider_lat = j.assigned_mechanic.lat
        provider_lon = j.assigned_mechanic.lon
    elif j.assigned_garage:
        provider_lat = j.assigned_garage.lat
        provider_lon = j.assigned_garage.lon

    return {
        "ok": True,
        "customerLat": j.customer_lat,
        "customerLon": j.customer_lon,
        "providerLat": provider_lat,
        "providerLon": provider_lon,
    }


# ── Disputes ──────────────────────────────────────────────────
@router.get("/disputes")
async def list_disputes(
    db: DbSession,
    user: AdminUser,
    status_filter: str | None = Query(None, alias="status"),
):
    q = (
        select(Dispute)
        .options(joinedload(Dispute.job))
        .options(joinedload(Dispute.job).joinedload(Job.customer))
        .options(joinedload(Dispute.job).joinedload(Job.assigned_mechanic))
        .options(joinedload(Dispute.job).joinedload(Job.assigned_garage))
    )
    if status_filter:
        try:
            ds = DisputeStatus(status_filter)
            q = q.where(Dispute.status == ds.value)
        except ValueError:
            pass
    q = q.order_by(Dispute.created_at.desc())
    result = await db.execute(q)
    rows = result.unique().scalars().all()

    dispute_list = []
    for d in rows:
        job = d.job
        customer_name = _user_display_name(job.customer) if job and job.customer else "Unknown"
        provider_name = "Unknown"
        amount = "—"
        if job:
            if job.assigned_mechanic:
                provider_name = job.assigned_mechanic.full_name
            elif job.assigned_garage:
                provider_name = job.assigned_garage.garage_name
            if job.total_amount is not None:
                amount = f"₹{job.total_amount:,.0f}"
            elif job.price is not None:
                amount = f"₹{job.price:,.0f}"

        dispute_list.append({
            "id": str(d.id),
            "jobId": str(d.job_id) if d.job_id else None,
            "customer": customer_name,
            "provider": provider_name,
            "reason": d.notes or "No description provided",
            "amount": amount,
            "status": d.status.capitalize() if d.status else "Open",
            "date": d.created_at.strftime("%d %b %Y, %I:%M %p") if d.created_at else "—",
            "desc": d.notes or "No description provided",
        })

    return {"disputes": dispute_list}


class DisputeUpdateBody(BaseModel):
    status: str = Field(pattern="^(open|investigating|resolved|dismissed)$")
    resolution: str | None = Field(None, max_length=2000)


@router.patch("/disputes/{dispute_id}")
@audit_log("update_dispute", "dispute", entity_id_arg="dispute_id")
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
    if body.status in ("resolved", "dismissed"):
        d.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return {"ok": True}


# ── Refund ────────────────────────────────────────────────────
class RefundBody(BaseModel):
    amount: int = Field(ge=0, le=50000000, description="Amount in paise")
    notes: str | None = Field(None, max_length=1000)


@router.post("/disputes/{dispute_id}/refund")
@audit_log("refund_dispute", "dispute", entity_id_arg="dispute_id")
async def refund_dispute(
    dispute_id: UUID,
    body: RefundBody,
    db: DbSession,
    user: AdminUser,
):
    result = await db.execute(
        select(Dispute).options(joinedload(Dispute.job)).where(Dispute.id == dispute_id)
    )
    d = result.unique().scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Dispute not found")

    # Update or create a refund payment
    job = d.job
    if job:
        # Mark existing payments as refunded
        payments_query = await db.execute(
            select(Payment).where(Payment.job_id == job.id, Payment.status == "captured")
        )
        existing_payments = payments_query.scalars().all()
        for p in existing_payments:
            p.status = "refunded"
            p.updated_at = datetime.now(timezone.utc)

    # Update dispute
    d.status = "resolved"
    resolution_note = f"Refund of ₹{body.amount / 100:,.0f} issued."
    if body.notes:
        resolution_note += f" Note: {body.notes}"
    d.resolution = (d.resolution or "") + ("\n" if d.resolution else "") + resolution_note
    d.resolved_at = datetime.now(timezone.utc)

    # Notify customer
    if job:
        db.add(Notification(
            user_id=job.user_id,
            title="Refund Issued",
            body=f"A refund of ₹{body.amount / 100:,.0f} has been issued for dispute {d.id}.",
            type="system",
            job_id=job.id,
        ))

    await db.flush()
    return {"ok": True, "message": f"Refund of ₹{body.amount / 100:,.0f} processed."}


# ── Penalize ──────────────────────────────────────────────────
class PenalizeBody(BaseModel):
    amount: int = Field(ge=0, le=50000000, description="Penalty amount in paise")
    notes: str | None = Field(None, max_length=1000)


@router.post("/disputes/{dispute_id}/penalize")
@audit_log("penalize_provider", "dispute", entity_id_arg="dispute_id")
async def penalize_provider(
    dispute_id: UUID,
    body: PenalizeBody,
    db: DbSession,
    user: AdminUser,
):
    result = await db.execute(
        select(Dispute).options(
            joinedload(Dispute.job).joinedload(Job.assigned_mechanic),
            joinedload(Dispute.job).joinedload(Job.assigned_garage),
        ).where(Dispute.id == dispute_id)
    )
    d = result.unique().scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Dispute not found")

    job = d.job
    provider_user_id = None

    if job and job.assigned_mechanic:
        job.assigned_mechanic.penalized = True
        job.assigned_mechanic.penalty_amount = body.amount
        provider_user_id = job.assigned_mechanic.user_id
    elif job and job.assigned_garage:
        job.assigned_garage.penalized = True
        job.assigned_garage.penalty_amount = body.amount
        provider_user_id = job.assigned_garage.user_id

    d.status = "resolved"
    penalty_note = f"Provider penalized ₹{body.amount / 100:,.0f}."
    if body.notes:
        penalty_note += f" Reason: {body.notes}"
    d.resolution = (d.resolution or "") + ("\n" if d.resolution else "") + penalty_note
    d.resolved_at = datetime.now(timezone.utc)

    if provider_user_id:
        db.add(Notification(
            user_id=provider_user_id,
            title="Penalty Applied",
            body=f"A penalty of ₹{body.amount / 100:,.0f} has been applied for dispute {d.id}.",
            type="system",
            job_id=job.id if job else None,
        ))

    await db.flush()
    return {"ok": True, "message": f"Provider penalized ₹{body.amount / 100:,.0f}."}


# ── Message Both Parties ──────────────────────────────────────
class MessageBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/disputes/{dispute_id}/message")
@audit_log("message_dispute_parties", "dispute", entity_id_arg="dispute_id")
async def message_dispute_parties(
    dispute_id: UUID,
    body: MessageBody,
    db: DbSession,
    user: AdminUser,
):
    result = await db.execute(
        select(Dispute).options(
            joinedload(Dispute.job).joinedload(Job.customer),
            joinedload(Dispute.job).joinedload(Job.assigned_mechanic),
            joinedload(Dispute.job).joinedload(Job.assigned_garage),
        ).where(Dispute.id == dispute_id)
    )
    d = result.unique().scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Dispute not found")

    job = d.job
    if not job:
        raise HTTPException(status_code=400, detail="Dispute has no associated job")

    # Notify customer
    customer_notif = Notification(
        user_id=job.user_id,
        title="Message from Admin regarding Dispute",
        body=body.message,
        type="system",
        job_id=job.id,
    )
    db.add(customer_notif)

    # Notify provider
    provider_user_id = None
    if job.assigned_mechanic:
        provider_user_id = job.assigned_mechanic.user_id
    elif job.assigned_garage:
        provider_user_id = job.assigned_garage.user_id

    if provider_user_id:
        db.add(Notification(
            user_id=provider_user_id,
            title="Message from Admin regarding Dispute",
            body=body.message,
            type="system",
            job_id=job.id,
        ))

    # Store the message in dispute notes
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg_log = f"\n[{timestamp}] Admin message: {body.message}"
    d.notes = (d.notes or "") + msg_log

    await db.flush()
    return {"ok": True, "message": "Notification sent to both parties."}


# ── Pricing ───────────────────────────────────────────────────
class PricingBody(BaseModel):
    service_type: str = Field(min_length=1, max_length=64)
    min_price: int = Field(ge=0, le=50000000)
    max_price: int = Field(ge=0, le=50000000)


@router.get("/pricing")
async def get_pricing(db: DbSession, user: AdminUser):
    return {"pricing": []}


@router.post("/pricing")
@audit_log("set_pricing", "pricing", entity_id_arg="")
async def set_pricing(body: PricingBody, db: DbSession, user: AdminUser):
    return {"ok": True, "service_type": body.service_type}
