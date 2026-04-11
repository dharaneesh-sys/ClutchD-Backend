import asyncio
import logging
from uuid import UUID

from celery import Celery

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "mechoncall",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="jobs.retry_assignment")
def retry_job_assignment(job_id: str) -> None:
    async def _run() -> None:
        from sqlalchemy import select

        from app.db.session import AsyncSessionLocal
        from app.models.job import Job
        from app.services.job_service import assign_job_auto

        async with AsyncSessionLocal() as session:
            r = await session.execute(select(Job).where(Job.id == UUID(job_id)))
            job = r.scalar_one_or_none()
            if job and job.status == "searching":
                await assign_job_auto(session, job)
                await session.commit()
                logger.info("Retried assignment for job %s", job_id)

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.exception("retry_job_assignment failed: %s", e)


@celery_app.task(name="notify.generic")
def send_notification(payload: dict) -> None:
    logger.info("Notification task: %s", payload)
