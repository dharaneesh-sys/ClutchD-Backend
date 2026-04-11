from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models.enums import UserRole
from app.schemas.job import PaymentCompleteBody, ServiceRequestCreate, ServiceRequestStatusUpdate
from app.services import job_service

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
    return await job_service.patch_job_status(db, job, body.status, mid)


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
    return await job_service.complete_job_with_payment(db, job, body.model_dump(exclude_none=True))


@router.post("/request/{job_id}/cancel")
async def cancel_request(job_id: UUID, db: DbSession, user: CurrentUser):
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await job_service.cancel_job(db, job)
    return {"ok": True}
