import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from celery import Celery
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "clutchd",
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
        from app.db.session import AsyncSessionLocal
        from app.models.job import Job
        from app.services.offer_service import create_provider_offers

        async with AsyncSessionLocal() as session:
            r = await session.execute(select(Job).where(Job.id == UUID(job_id)))
            job = r.scalar_one_or_none()
            if job and job.status == "searching":
                await create_provider_offers(session, job)
                await session.commit()
                logger.info("Retried assignment for job %s", job_id)

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.exception("retry_job_assignment failed: %s", e)


@celery_app.task(name="notify.generic")
def send_notification(payload: dict) -> None:
    logger.info("Notification task: %s", payload)


@celery_app.task(name="notify.new_offer", max_retries=1, default_retry_delay=30)
def dispatch_offer_push(job_id: str, expected_count: int) -> None:
    """Send FCM push notifications to providers who received offers for this job.

    Queries ProviderOffer records for the job, finds the users behind each
    provider, gathers their active device tokens, respects push_notifications
    preference, sends FCM push, deactivates invalid tokens, and creates
    in-app Notification records.
    """
    async def _run() -> None:
        from app.db.session import AsyncSessionLocal
        from app.models.device_token import DeviceToken
        from app.models.garage import Garage
        from app.models.job import Job
        from app.models.mechanic import Mechanic
        from app.models.notification import Notification
        from app.models.provider_offer import ProviderOffer
        from app.services import matching
        from app.services.push_service import send_new_offer_batch

        async with AsyncSessionLocal() as db:
            # Load job
            r = await db.execute(select(Job).where(Job.id == UUID(job_id)))
            job = r.scalar_one_or_none()
            if not job:
                logger.warning("dispatch_offer_push: job %s not found", job_id)
                return

            # Load all pending offers for this job
            r = await db.execute(
                select(ProviderOffer)
                .where(ProviderOffer.job_id == job.id, ProviderOffer.status == "pending")
            )
            offers = r.scalars().all()

            if not offers:
                logger.info("dispatch_offer_push: no pending offers for job %s", job_id)
                return

            # Collect provider user_ids and token-offer pairs
            token_offer_pairs: list[tuple[DeviceToken, ProviderOffer, float]] = []
            provider_user_ids: set[UUID] = set()

            for offer in offers:
                if offer.provider_type == "mechanic":
                    r = await db.execute(
                        select(Mechanic).where(Mechanic.id == offer.provider_id)
                    )
                    provider = r.scalar_one_or_none()
                else:
                    r = await db.execute(
                        select(Garage).where(Garage.id == offer.provider_id)
                    )
                    provider = r.scalar_one_or_none()

                if not provider:
                    continue

                provider_user_ids.add(provider.user_id)
                dist = matching.haversine_m(
                    job.customer_lat, job.customer_lon,
                    provider.lat, provider.lon,
                )

                # Query device tokens for this provider's user
                r = await db.execute(
                    select(DeviceToken)
                    .where(
                        DeviceToken.user_id == provider.user_id,
                        DeviceToken.is_active == True,
                    )
                )
                tokens = r.scalars().all()
                for dt in tokens:
                    token_offer_pairs.append((dt, offer, dist))

            if not token_offer_pairs:
                logger.info(
                    "dispatch_offer_push: no active device tokens for job %s providers",
                    job_id,
                )
            else:
                success, failure = await send_new_offer_batch(
                    db, job=job, token_offer_pairs=token_offer_pairs
                )
                logger.info(
                    "dispatch_offer_push: job=%s offers=%d tokens=%d success=%d failure=%d",
                    job_id, len(offers), len(token_offer_pairs), success, failure,
                )

            # Create in-app Notification records for each provider user
            for uid in provider_user_ids:
                notif = Notification(
                    user_id=uid,
                    title="New Service Request",
                    body=f"{job.issue_tag} — tap to view details",
                    type="job_update",
                    job_id=job.id,
                )
                db.add(notif)

            await db.commit()
            logger.info(
                "dispatch_offer_push: created %d in-app notifications for job %s",
                len(provider_user_ids), job_id,
            )

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.exception("dispatch_offer_push failed: %s", e)
