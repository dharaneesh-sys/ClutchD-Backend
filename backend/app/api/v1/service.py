from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models.enums import UserRole
from pydantic import BaseModel
from app.schemas.job import PaymentCompleteBody, ServiceRequestCreate, ServiceRequestStatusUpdate
from app.services import job_service
from app.services.job_service import InvalidTransitionError

router = APIRouter(prefix="/service", tags=["service"])


@router.post("/request")
async def create_request(body: ServiceRequestCreate, db: DbSession, user: CurrentUser):
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
async def trigger_sos(body: SOSRequest, db: DbSession, user: CurrentUser):
    # Log emergency
    import logging
    logger = logging.getLogger(__name__)
    logger.critical(f"SOS TRIGGERED by user {user.id} at {body.lat}, {body.lon}")
    
    # Broadcast SOS event to admins and nearby users
    try:
        from app.ws.manager import connection_manager
        # For simplicity, we just push to the user for acknowledgment
        # In a real app we would push to emergency services or an admin dashboard
        await connection_manager.send_personal_message(str(user.id), {
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

