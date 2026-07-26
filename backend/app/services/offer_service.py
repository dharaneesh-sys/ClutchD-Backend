import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Insert as PGInsert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.garage import Garage
from app.models.job import Job
from app.models.mechanic import Mechanic
from app.models.provider_offer import ProviderOffer
from app.models.user import User
from app.services import matching
from app.ws.manager import push_status_update

logger = logging.getLogger(__name__)


async def create_provider_offers(db: AsyncSession, job: Job) -> int:
    """Find eligible mechanics/garages and create ProviderOffer records.

    Acquires a FOR UPDATE lock on the job (must be in "searching" state),
    queries nearest matching providers based on request_type, and inserts
    deduplicated offers with a 15-minute expiry.

    Returns the number of offers created, or 0 if the job was already locked
    (assigned/cancelled) or no eligible providers were found.
    """
    # 1. Acquire FOR UPDATE lock — fail fast if already assigned/cancelled
    r = await db.execute(
        select(Job)
        .where(Job.id == job.id, Job.status == "searching")
        .with_for_update()
    )
    locked_job = r.scalar_one_or_none()
    if not locked_job:
        return 0

    settings = get_settings()

    # 2. Query eligible providers
    lat, lon = locked_job.customer_lat, locked_job.customer_lon
    issue_tag = locked_job.issue_tag

    mechs = await matching.nearest_mechanics(
        db, lat, lon, limit=10, issue_tag=issue_tag
    )
    gars = await matching.nearest_garages(
        db, lat, lon, limit=10, issue_tag=issue_tag
    )

    # 3. Determine which provider types to create offers for
    create_mechanics = locked_job.request_type in ("mechanic", "auto")
    create_garages = locked_job.request_type in ("garage", "auto")

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    total = 0

    # 4. Insert offers with ON CONFLICT DO NOTHING
    if create_mechanics:
        for m in mechs:
            stmt = (
                PGInsert(ProviderOffer)
                .values(
                    job_id=locked_job.id,
                    provider_type="mechanic",
                    provider_id=m.id,
                    expires_at=expires_at,
                    status="pending",
                )
                .on_conflict_do_nothing(constraint="uq_provider_offer")
            )
            r = await db.execute(stmt)
            total += r.rowcount

    if create_garages:
        for g in gars:
            stmt = (
                PGInsert(ProviderOffer)
                .values(
                    job_id=locked_job.id,
                    provider_type="garage",
                    provider_id=g.id,
                    expires_at=expires_at,
                    status="pending",
                )
                .on_conflict_do_nothing(constraint="uq_provider_offer")
            )
            r = await db.execute(stmt)
            total += r.rowcount

    # 5. Dispatch push notification Celery task
    if total > 0:
        from app.tasks.worker import dispatch_offer_push
        dispatch_offer_push.apply_async(args=[str(locked_job.id), total], countdown=2)

    return total


async def _resolve_provider(
    db: AsyncSession, user: User,
) -> tuple[str, int]:
    """Resolve authenticated user to (provider_type, provider_id).

    Raises 403 if the user has no provider profile matching their role.
    """
    if user.role == "mechanic":
        r = await db.execute(
            select(Mechanic).where(Mechanic.user_id == user.id)
        )
        mech = r.scalar_one_or_none()
        if not mech:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No mechanic profile")
        return "mechanic", mech.id
    elif user.role == "garage":
        r = await db.execute(
            select(Garage).where(Garage.user_id == user.id)
        )
        gar = r.scalar_one_or_none()
        if not gar:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No garage profile")
        return "garage", gar.id
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only mechanics and garages can accept offers")


async def accept_offer(db: AsyncSession, user: User, offer_id: UUID) -> dict:
    """Atomically accept a provider offer and assign the job.

    Uses a SELECT ... FOR UPDATE lock on the job row to guarantee that
    only one concurrent accept succeeds. The losing callers receive an
    HTTP 409 Conflict response.

    Returns a dict with the accepted offer and job details.
    """
    provider_type, provider_id = await _resolve_provider(db, user)

    r = await db.execute(
        select(ProviderOffer).where(
            ProviderOffer.id == offer_id,
            ProviderOffer.provider_type == provider_type,
            ProviderOffer.provider_id == provider_id,
        )
    )
    offer = r.scalar_one_or_none()
    if not offer:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Offer not found or does not belong to you",
        )

    if offer.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Offer is already {offer.status}",
        )

    if offer.expires_at and offer.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="Offer has expired",
        )

    r = await db.execute(
        select(Job)
        .where(Job.id == offer.job_id, Job.status == "searching")
        .with_for_update()
    )
    locked_job = r.scalar_one_or_none()
    if not locked_job:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This job has already been assigned to another provider",
        )

    offer.status = "accepted"
    locked_job.status = "assigned"
    locked_job.assigned_provider_type = provider_type
    if provider_type == "mechanic":
        locked_job.assigned_mechanic_id = provider_id
    elif provider_type == "garage":
        locked_job.assigned_garage_id = provider_id

    await db.flush()

    # Build provider summary for real-time push to customer
    provider_info: dict | None = None
    if provider_type == "mechanic":
        r = await db.execute(select(Mechanic).where(Mechanic.id == provider_id))
        mech = r.scalar_one_or_none()
        if mech:
            dist_str = "Unknown distance"
            if locked_job.customer_lat is not None:
                try:
                    dist = matching.haversine_m(
                        locked_job.customer_lat, locked_job.customer_lon,
                        mech.lat, mech.lon,
                    )
                    dist_str = f"{dist / 1000:.1f} km"
                except Exception:
                    pass
            provider_info = {
                "id": str(mech.id),
                "name": mech.full_name,
                "rating": mech.rating,
                "distance": dist_str,
            }
    elif provider_type == "garage":
        r = await db.execute(select(Garage).where(Garage.id == provider_id))
        gar = r.scalar_one_or_none()
        if gar:
            dist_str = "Unknown distance"
            if locked_job.customer_lat is not None:
                try:
                    dist = matching.haversine_m(
                        locked_job.customer_lat, locked_job.customer_lon,
                        gar.lat, gar.lon,
                    )
                    dist_str = f"{dist / 1000:.1f} km"
                except Exception:
                    pass
            provider_info = {
                "id": str(gar.id),
                "name": gar.garage_name,
                "rating": gar.rating,
                "distance": dist_str,
            }

    await push_status_update(
        str(locked_job.user_id),
        str(locked_job.id),
        "assigned",
        provider_info,
    )

    return {
        "id": offer.id,
        "status": "accepted",
        "job_id": locked_job.id,
        "job_status": "assigned",
        "message": "Offer accepted, job assigned",
    }


async def decline_offer(db: AsyncSession, user: User, offer_id: UUID) -> dict:
    """Decline a provider offer.

    Does NOT affect the job — the customer's request stays active and
    other providers can still accept their own offers.
    """
    provider_type, provider_id = await _resolve_provider(db, user)

    r = await db.execute(
        select(ProviderOffer).where(
            ProviderOffer.id == offer_id,
            ProviderOffer.provider_type == provider_type,
            ProviderOffer.provider_id == provider_id,
        )
    )
    offer = r.scalar_one_or_none()
    if not offer:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Offer not found or does not belong to you",
        )

    if offer.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Offer is already {offer.status}",
        )

    offer.status = "declined"
    await db.flush()

    return {
        "id": offer.id,
        "status": "declined",
        "job_id": offer.job_id,
        "job_status": "searching",  # Job unaffected by decline
        "message": "Offer declined",
    }
