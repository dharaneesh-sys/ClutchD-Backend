"""Push notification service — FCM delivery for provider offers."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_token import DeviceToken
from app.models.job import Job
from app.models.provider_offer import ProviderOffer
from app.services import matching

logger = logging.getLogger(__name__)

try:
    from firebase_admin import exceptions as fb_exc
    from firebase_admin import messaging
except ImportError:
    messaging = None  # type: ignore
    fb_exc = None  # type: ignore


async def send_new_offer_push(
    db: AsyncSession,
    *,
    device_token: DeviceToken,
    job: Job,
    offer: ProviderOffer,
    distance_m: float,
) -> bool:
    """Send an FCM push notification for a new offer to a single device.

    Returns True if the message was sent successfully.
    Returns False if the token was invalid (and deactivates it) or if
    the user has disabled push notifications.
    """
    # Skip inactive tokens
    if not device_token.is_active:
        return False

    # Check user push notification preference
    from app.models.new_models import UserSettings

    r = await db.execute(
        select(UserSettings).where(UserSettings.user_id == device_token.user_id)
    )
    settings = r.scalar_one_or_none()
    if settings and not settings.push_notifications:
        return False

    if messaging is None:
        logger.warning("firebase-admin not installed — cannot send push")
        return False

    # Build notification payload
    distance_km = distance_m / 1000.0
    title = "New Service Request"
    body = f"{job.issue_tag} \u2022 {distance_km:.1f} km away"

    data = {
        "type": "new_offer",
        "offerId": str(offer.id),
        "jobId": str(job.id),
    }

    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data=data,
        token=device_token.token,
    )

    try:
        messaging.send(message)
        return True
    except messaging.UnregisteredError:
        logger.info("Token unregistered — deactivating device_token %s", device_token.id)
        device_token.is_active = False
        await db.flush()
        return False
    except messaging.SenderIdMismatchError:
        logger.info("Sender ID mismatch — deactivating device_token %s", device_token.id)
        device_token.is_active = False
        await db.flush()
        return False
    except messaging.ThirdPartyAuthError:
        logger.info("Third-party auth error — deactivating device_token %s", device_token.id)
        device_token.is_active = False
        await db.flush()
        return False
    except fb_exc.UnavailableError:
        logger.warning("FCM temporarily unavailable — skipping push to %s", device_token.id)
        return False
    except fb_exc.FirebaseError as e:
        logger.error("FCM send failed for token %s: %s", device_token.id, e)
        return False


async def send_new_offer_batch(
    db: AsyncSession,
    *,
    job: Job,
    token_offer_pairs: list[tuple[DeviceToken, ProviderOffer, float]],
) -> tuple[int, int]:
    """Send FCM pushes for a new offer to multiple devices.

    Returns (success_count, failure_count).
    Invalid tokens are deactivated automatically.
    """
    if messaging is None:
        logger.warning("firebase-admin not installed — cannot send batch push")
        return 0, len(token_offer_pairs)

    from app.models.new_models import UserSettings

    # Filter by is_active + push_notifications preference
    valid_pairs: list[tuple[DeviceToken, ProviderOffer, float]] = []
    for dt, offer, dist in token_offer_pairs:
        if not dt.is_active:
            continue
        r = await db.execute(
            select(UserSettings).where(UserSettings.user_id == dt.user_id)
        )
        settings = r.scalar_one_or_none()
        if settings and not settings.push_notifications:
            continue
        valid_pairs.append((dt, offer, dist))

    if not valid_pairs:
        return 0, 0

    # Build batch message (all get the same notification — each offer is individual)
    success = 0
    failure = 0

    for dt, offer, dist in valid_pairs:
        ok = await send_new_offer_push(
            db, device_token=dt, job=job, offer=offer, distance_m=dist
        )
        if ok:
            success += 1
        else:
            failure += 1

    return success, failure
