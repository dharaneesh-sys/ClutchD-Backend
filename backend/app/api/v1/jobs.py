import io
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_admin
from app.models.enums import UserRole
from app.models.job import Job
from app.models.user import User
from app.schemas.job import JobAssignRequest, ServiceRequestCreate
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])

AdminUser = Annotated[User, Depends(require_admin)]


@router.post("/create")
async def jobs_create(body: ServiceRequestCreate, db: DbSession, user: CurrentUser):
    if user.role != UserRole.customer.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customers only")
    data = body.model_dump(exclude_none=True)
    return await job_service.create_service_request(db, user, data)


@router.post("/assign")
async def jobs_assign(body: JobAssignRequest, db: DbSession, user: AdminUser):
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


@router.get("/incoming")
async def incoming_jobs(db: DbSession, user: CurrentUser):
    """Jobs assigned to the current mechanic or garage, or customer's own jobs."""
    from sqlalchemy import select

    from app.models.garage import Garage
    from app.models.job import Job
    from app.models.mechanic import Mechanic

    if user.role == UserRole.mechanic.value:
        mr = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        mech = mr.scalar_one_or_none()
        if not mech:
            return {"jobs": []}
        r = await db.execute(
            select(Job)
            .where(
                Job.assigned_mechanic_id == mech.id,
                Job.status.in_(("assigned", "en_route", "in_progress")),
            )
            .order_by(Job.created_at.desc())
        )
    elif user.role == UserRole.garage.value:
        gr = await db.execute(select(Garage).where(Garage.user_id == user.id))
        garage = gr.scalar_one_or_none()
        if not garage:
            return {"jobs": []}
        r = await db.execute(
            select(Job)
            .where(
                Job.assigned_garage_id == garage.id,
                Job.status.in_(("assigned", "en_route", "in_progress")),
            )
            .order_by(Job.created_at.desc())
        )
    elif user.role == UserRole.customer.value:
        r = await db.execute(
            select(Job)
            .where(
                Job.user_id == user.id,
                Job.status.in_(("searching", "assigned", "en_route", "in_progress")),
            )
            .order_by(Job.created_at.desc())
        )
    else:
        # Admin sees all active jobs
        r = await db.execute(
            select(Job)
            .where(Job.status.in_(("searching", "assigned", "en_route", "in_progress")))
            .order_by(Job.created_at.desc())
            .limit(50)
        )

    jobs = r.scalars().all()
    return {"jobs": [job_service.job_response_dict(j) for j in jobs]}


@router.get("/history")
async def get_history(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    from sqlalchemy import select

    from app.models.garage import Garage
    from app.models.job import Job
    from app.models.mechanic import Mechanic

    if user.role == UserRole.mechanic.value:
        mr = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        mech = mr.scalar_one_or_none()
        if not mech:
            return {"jobs": []}
        r = await db.execute(
            select(Job)
            .where(Job.assigned_mechanic_id == mech.id, Job.status.in_(("completed", "cancelled")))
            .order_by(Job.created_at.desc())
            .offset(offset).limit(limit)
        )
    elif user.role == UserRole.garage.value:
        gr = await db.execute(select(Garage).where(Garage.user_id == user.id))
        garage = gr.scalar_one_or_none()
        if not garage:
            return {"jobs": []}
        r = await db.execute(
            select(Job)
            .where(Job.assigned_garage_id == garage.id, Job.status.in_(("completed", "cancelled")))
            .order_by(Job.created_at.desc())
            .offset(offset).limit(limit)
        )
    elif user.role == UserRole.customer.value:
        r = await db.execute(
            select(Job)
            .where(Job.user_id == user.id, Job.status.in_(("completed", "cancelled")))
            .order_by(Job.created_at.desc())
            .offset(offset).limit(limit)
        )
    else:
        r = await db.execute(
            select(Job)
            .where(Job.status.in_(("completed", "cancelled")))
            .order_by(Job.created_at.desc())
            .offset(offset).limit(limit)
        )

    jobs = r.scalars().all()
    return {"jobs": [job_service.job_response_dict(j) for j in jobs]}


@router.get("/history/{job_id}/invoice")
async def get_invoice(job_id: UUID, db: DbSession, user: CurrentUser):
    # Ensure job belongs to user
    job = await job_service.get_job_for_user(db, job_id, user)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Cannot generate invoice for incomplete job")

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        raise HTTPException(status_code=501, detail="PDF generation not available on server")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    
    # Header
    c.setFont("Helvetica-Bold", 24)
    c.drawString(50, 750, "ClutchD - Service Invoice")
    
    # Details
    c.setFont("Helvetica", 12)
    c.drawString(50, 700, f"Invoice Number: INV-{str(job.id)[:8].upper()}")
    c.drawString(50, 680, f"Date: {job.updated_at.strftime('%Y-%m-%d %H:%M') if job.updated_at else 'Unknown'}")
    c.drawString(50, 660, f"Customer ID: {str(job.user_id)[:8]}")
    
    # Job Information
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 620, "Service Details")
    c.setFont("Helvetica", 12)
    c.drawString(50, 600, f"Issue: {job.issue_tag}")
    c.drawString(50, 580, f"Description: {job.description[:100]}...")
    
    c.setFont("Helvetica-Bold", 18)
    # Estimate from Job price since actual payment might be elsewhere to simplify
    amount = 0
    if job.price_estimate:
        amount = job.price_estimate.get("min", 0)
    c.drawString(50, 530, f"Total Paid: INR {amount}")
    
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(50, 50, "Thank you for using ClutchD On-Demand Mechanic Service.")
    
    c.save()
    buffer.seek(0)
    
    return StreamingResponse(
        buffer, 
        media_type="application/pdf", 
        headers={"Content-Disposition": f"attachment; filename=invoice_{str(job.id)[:8]}.pdf"}
    )

