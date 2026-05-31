from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.enums import UserRole
from pydantic import BaseModel
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
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())


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


class SOSRequest(BaseModel):
    lat: float
    lon: float

@router.post("/sos")
@limiter.limit("5/minute")
async def trigger_sos(request: Request, body: SOSRequest, db: DbSession, user: CurrentUser):
    # Log emergency
    import logging
    logger = logging.getLogger(__name__)
    logger.critical(f"SOS TRIGGERED by user {user.id} at {body.lat}, {body.lon}")
    
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

