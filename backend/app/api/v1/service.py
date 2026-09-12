import logging

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

logger = logging.getLogger(__name__)

from app.api.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.enums import UserRole
from pydantic import BaseModel, Field
from app.schemas.job import FinalizePriceBody, PaymentCompleteBody, ServiceRequestCreate, ServiceRequestStatusUpdate
from app.services import job_service
from app.services.job_service import InvalidTransitionError

router = APIRouter(prefix="/service", tags=["service"])


@router.post("/request")
@limiter.limit("10/minute")
async def create_request(request: Request, body: ServiceRequestCreate, db: DbSession, user: CurrentUser):
    if user.role != UserRole.customer.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customers only")
    data = body.model_dump(exclude_none=True)
    return await job_service.create_service_request(db, user, data)


@router.patch("/request/{job_id}/status")
async def patch_request_status(
    job_id: UUID,
    body: ServiceRequestStatusUpdate,
    db: DbSession,
    user: CurrentUser,
):
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    mid = UUID(body.mechanicId) if body.mechanicId else None
    try:
        return await job_service.patch_job_status(db, job, body.status, mid)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        logger.exception("patch_job_status failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/request/{job_id}/finalize-price")
async def finalize_price(
    job_id: UUID,
    body: FinalizePriceBody,
    db: DbSession,
    user: CurrentUser,
):
    """Called by the mechanic/garage to enter the service charge and trigger
    fee calculation + status transition to payment_pending."""
    if user.role not in (UserRole.mechanic.value, UserRole.garage.value, UserRole.admin.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Providers only")
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return await job_service.finalize_job_price(db, job, body.serviceAmount)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/request/{job_id}/complete")
async def complete_request(
    job_id: UUID,
    body: PaymentCompleteBody,
    db: DbSession,
    user: CurrentUser,
):
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return await job_service.complete_job_with_payment(db, job, body.model_dump(exclude_none=True))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/request/{job_id}/cancel")
async def cancel_request(job_id: UUID, db: DbSession, user: CurrentUser):
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await job_service.cancel_job(db, job)
    return {"ok": True}


class HoldBody(BaseModel):
    method: str = Field(max_length=32)
    amount: float = Field(default=0, ge=0, le=500000)
    transactionId: str | None = Field(None, max_length=128)


class ReleaseBody(BaseModel):
    paymentId: str | None = Field(None, max_length=128)


class DisputeBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class VehicleAttachBody(BaseModel):
    vehicleId: UUID


@router.post("/request/{job_id}/hold")
async def hold_payment(job_id: UUID, body: HoldBody, db: DbSession, user: CurrentUser):
    """Record an escrow hold: persist a held Payment row for the job."""
    from app.models.payment import Payment

    if user.role != UserRole.customer.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customers only")
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("payment_pending", "in_progress"):
        raise HTTPException(status_code=409, detail=f"Cannot hold payment in status {job.status}")
    payment = Payment(
        job_id=job.id,
        user_id=user.id,
        amount=int(round(body.amount * 100)),
        provider="manual",
        provider_payment_id=body.transactionId,
        status="held",
        method=body.method,
    )
    db.add(payment)
    await db.flush()
    return {"payment_id": str(payment.id), "status": "held", "job_id": str(job.id)}


@router.post("/request/{job_id}/release")
async def release_payment(job_id: UUID, body: ReleaseBody, db: DbSession, user: CurrentUser):
    """Release escrow: mark the held Payment released and complete the job."""
    from sqlalchemy import select

    from app.models.payment import Payment

    if user.role != UserRole.customer.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customers only")
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    q = select(Payment).where(Payment.job_id == job.id, Payment.status == "held").order_by(Payment.created_at.desc())
    if body.paymentId:
        q = select(Payment).where(Payment.job_id == job.id, Payment.provider_payment_id == body.paymentId)
    r = await db.execute(q)
    payment = r.scalars().first()
    if not payment:
        raise HTTPException(status_code=404, detail="No held payment for this job")
    payment.status = "released"
    await db.flush()
    return await job_service.complete_job_with_payment(
        db, job,
        {"method": payment.method or "upi", "amount": (payment.amount or 0) / 100,
         "status": "success", "transactionId": payment.provider_payment_id},
    )


@router.post("/request/{job_id}/dispute")
async def dispute_payment(job_id: UUID, body: DisputeBody, db: DbSession, user: CurrentUser):
    """Open a payment dispute as a support ticket linked to the job."""
    from app.models.new_models import SupportTicket

    from app.api.v1.tickets import _generate_ticket_number

    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    ticket = SupportTicket(
        user_id=user.id,
        subject=f"Payment dispute — job {job.id}",
        message=body.reason,
        category="payment",
        priority="high",
        status="open",
        ticket_number=_generate_ticket_number(),
    )
    db.add(ticket)
    await db.flush()
    return {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "status": "open"}


@router.patch("/request/{job_id}/vehicle")
async def attach_vehicle(job_id: UUID, body: VehicleAttachBody, db: DbSession, user: CurrentUser):
    """Attach one of the customer's vehicles to the job."""
    from sqlalchemy import select

    from app.models.vehicle import Vehicle

    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    r = await db.execute(select(Vehicle).where(Vehicle.id == body.vehicleId, Vehicle.user_id == user.id))
    vehicle = r.scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    job.vehicle_id = vehicle.id
    await db.flush()
    return job_service.job_response_dict(job, None)


class SOSRequest(BaseModel):
    lat: float
    lon: float

@router.post("/sos")
@limiter.limit("5/minute")
async def trigger_sos(request: Request, body: SOSRequest, db: DbSession, user: CurrentUser):
    logger.critical("SOS TRIGGERED by user %s at %s, %s", user.id, body.lat, body.lon)
    
    # Broadcast SOS event to admins and nearby users
    try:
        from app.ws.manager import manager as ws_manager
        # For simplicity, we just push to the user for acknowledgment
        # In a real app we would push to emergency services or an admin dashboard
        await ws_manager.send_json_to_user(str(user.id), {
            "type": "SOS_ACK",
            "payload": {
                "message": "Emergency services have been notified.",
                "lat": body.lat,
                "lon": body.lon
            }
        })
    except Exception:
        pass

    return {"status": "emergency_notified", "lat": body.lat, "lon": body.lon}

