from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models.enums import UserRole
from app.schemas.job import JobAssignRequest, ServiceRequestCreate
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/create")
async def jobs_create(body: ServiceRequestCreate, db: DbSession, user: CurrentUser):
    if user.role != UserRole.customer.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customers only")
    data = body.model_dump(exclude_none=True)
    return await job_service.create_service_request(db, user, data)


@router.post("/assign")
async def jobs_assign(body: JobAssignRequest, db: DbSession, user: CurrentUser):
    if user.role not in (UserRole.admin.value,) and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin only")
    from sqlalchemy import select

    from app.models.job import Job

    r = await db.execute(select(Job).where(Job.id == body.job_id))
    job = r.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.assigned_type = body.assign_type
    if body.assign_type == "mechanic":
        job.assigned_mechanic_id = body.assign_id
        job.assigned_garage_id = None
    else:
        job.assigned_garage_id = body.assign_id
        job.assigned_mechanic_id = None
    job.status = "assigned"
    await db.flush()
    return job_service.job_response_dict(job, None)


@router.get("/status/{job_id}")
async def job_status(job_id: UUID, db: DbSession, user: CurrentUser):
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    mech_summary = None
    if job.assigned_mechanic_id:
        from sqlalchemy import select

        from app.models.mechanic import Mechanic

        r = await db.execute(select(Mechanic).where(Mechanic.id == job.assigned_mechanic_id))
        m = r.scalar_one_or_none()
        mech_summary = job_service.assignee_summary(job, m)
    elif job.assigned_garage_id:
        from sqlalchemy import select

        from app.models.garage import Garage

        r = await db.execute(select(Garage).where(Garage.id == job.assigned_garage_id))
        g = r.scalar_one_or_none()
        mech_summary = job_service.assignee_summary(job, g)
    return job_service.job_response_dict(job, mech_summary)
