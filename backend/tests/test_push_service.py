"""Tests for the push notification service layer.

These tests mock the firebase_admin.messaging module since no real
FCM credentials are available in CI/test environments.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_token import DeviceToken
from app.models.job import Job
from app.models.provider_offer import ProviderOffer
from app.models.user import User
from app.services.push_service import send_new_offer_batch, send_new_offer_push

pytestmark = pytest.mark.asyncio


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def device_token_pair(
    db_session: AsyncSession, test_user: User
) -> tuple[DeviceToken, User]:
    """Create an active device token for test_user."""
    dt = DeviceToken(
        id=uuid.uuid4(),
        user_id=test_user.id,
        token=f"fcm-test-{uuid.uuid4().hex}",
        platform="android",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(dt)
    await db_session.commit()
    await db_session.refresh(dt)
    return dt, test_user


@pytest_asyncio.fixture
async def inactive_token(
    db_session: AsyncSession, test_user: User
) -> DeviceToken:
    """Create an inactive device token."""
    dt = DeviceToken(
        id=uuid.uuid4(),
        user_id=test_user.id,
        token=f"fcm-inactive-{uuid.uuid4().hex}",
        platform="ios",
        is_active=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(dt)
    await db_session.commit()
    await db_session.refresh(dt)
    return dt


@pytest_asyncio.fixture
async def test_job_with_offer(
    db_session: AsyncSession,
    test_user: User,
) -> Job:
    """Create a job with a pending offer for push context.

    Note: ProviderOffer has no ``updated_at`` column.
    """
    job = Job(
        id=uuid.uuid4(),
        user_id=test_user.id,
        issue_tag="flat_tire",
        description="Rear left tire",
        request_type="auto",
        status="searching",
        customer_lat=28.6139,
        customer_lon=77.2090,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.flush()

    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=job.id,
        provider_type="mechanic",
        provider_id=uuid.uuid4(),
        status="pending",
        expires_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)
    await db_session.commit()
    await db_session.refresh(job)
    return job


# ── Tests ────────────────────────────────────────────────────────────


async def test_send_push_skips_inactive_token(
    db_session: AsyncSession,
    inactive_token: DeviceToken,
    test_job_with_offer: Job,
):
    """PS1: Inactive tokens are skipped without attempting FCM send."""
    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job_with_offer.id,
        provider_type="mechanic",
        provider_id=uuid.uuid4(),
        status="pending",
        expires_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)

    result = await send_new_offer_push(
        db_session,
        device_token=inactive_token,
        job=test_job_with_offer,
        offer=offer,
        distance_m=5000,
    )
    assert result is False


@patch("app.services.push_service.messaging")
async def test_send_push_success(
    mock_messaging: MagicMock,
    db_session: AsyncSession,
    device_token_pair: tuple[DeviceToken, User],
    test_job_with_offer: Job,
):
    """PS2: Successful FCM send returns True."""
    dt, _user = device_token_pair
    mock_messaging.Message = MagicMock()
    mock_messaging.Notification = MagicMock()
    mock_messaging.send = MagicMock(return_value="projects/.../messages/msg_id_123")

    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job_with_offer.id,
        provider_type="mechanic",
        provider_id=uuid.uuid4(),
        status="pending",
        expires_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)

    result = await send_new_offer_push(
        db_session,
        device_token=dt,
        job=test_job_with_offer,
        offer=offer,
        distance_m=5000,
    )
    assert result is True


@patch("app.services.push_service.messaging")
async def test_send_push_unregistered_error(
    mock_messaging: MagicMock,
    db_session: AsyncSession,
    device_token_pair: tuple[DeviceToken, User],
    test_job_with_offer: Job,
):
    """PS3: UnregisteredError deactivates the token and returns False."""
    dt, _user = device_token_pair
    mock_messaging.Message = MagicMock()
    mock_messaging.Notification = MagicMock()

    class MockUnregisteredError(Exception):
        pass

    mock_messaging.UnregisteredError = MockUnregisteredError
    mock_messaging.send = MagicMock(
        side_effect=MockUnregisteredError("Not registered")
    )

    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job_with_offer.id,
        provider_type="mechanic",
        provider_id=uuid.uuid4(),
        status="pending",
        expires_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)

    result = await send_new_offer_push(
        db_session,
        device_token=dt,
        job=test_job_with_offer,
        offer=offer,
        distance_m=5000,
    )
    assert result is False

    await db_session.refresh(dt)
    assert dt.is_active is False


@patch("app.services.push_service.fb_exc")
@patch("app.services.push_service.messaging")
async def test_send_push_generic_firebase_error(
    mock_messaging: MagicMock,
    mock_fb_exc: MagicMock,
    db_session: AsyncSession,
    device_token_pair: tuple[DeviceToken, User],
    test_job_with_offer: Job,
):
    """PS4: Generic FirebaseError is caught gracefully (token stays active).

    We patch both messaging and fb_exc so that our MockFirebaseError
    is caught by the ``except fb_exc.FirebaseError`` handler.
    """
    dt, _user = device_token_pair
    mock_messaging.Message = MagicMock()
    mock_messaging.Notification = MagicMock()
    mock_messaging.UnregisteredError = type("UnregisteredError", (Exception,), {})
    mock_messaging.SenderIdMismatchError = type(
        "SenderIdMismatchError", (Exception,), {}
    )
    mock_messaging.ThirdPartyAuthError = type(
        "ThirdPartyAuthError", (Exception,), {}
    )

    class MockFirebaseError(Exception):
        pass

    mock_fb_exc.FirebaseError = MockFirebaseError
    mock_fb_exc.UnavailableError = type("UnavailableError", (Exception,), {})
    mock_messaging.send = MagicMock(side_effect=MockFirebaseError("boom"))

    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job_with_offer.id,
        provider_type="mechanic",
        provider_id=uuid.uuid4(),
        status="pending",
        expires_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)

    result = await send_new_offer_push(
        db_session,
        device_token=dt,
        job=test_job_with_offer,
        offer=offer,
        distance_m=5000,
    )
    assert result is False

    await db_session.refresh(dt)
    assert dt.is_active is True


@patch("app.services.push_service.messaging")
async def test_batch_send_counts(
    mock_messaging: MagicMock,
    db_session: AsyncSession,
    test_job_with_offer: Job,
    test_user: User,
):
    """PS5: Batch send returns correct success/failure counts."""
    mock_messaging.Message = MagicMock()
    mock_messaging.Notification = MagicMock()

    class MockUnregisteredError(Exception):
        pass

    mock_messaging.UnregisteredError = MockUnregisteredError
    mock_messaging.SenderIdMismatchError = type(
        "SenderIdMismatchError", (Exception,), {}
    )
    mock_messaging.ThirdPartyAuthError = type(
        "ThirdPartyAuthError", (Exception,), {}
    )
    mock_messaging.send = MagicMock(return_value="ok")

    pairs = []
    for i in range(2):
        dt = DeviceToken(
            id=uuid.uuid4(),
            user_id=test_user.id,
            token=f"fcm-batch-{i}-{uuid.uuid4().hex}",
            platform="android",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(dt)
        await db_session.flush()
        pairs.append(dt)

    offer = ProviderOffer(
        id=uuid.uuid4(),
        job_id=test_job_with_offer.id,
        provider_type="mechanic",
        provider_id=uuid.uuid4(),
        status="pending",
        expires_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)

    token_offer_pairs = [(pairs[0], offer, 3000.0), (pairs[1], offer, 3000.0)]

    success, failure = await send_new_offer_batch(
        db_session, job=test_job_with_offer, token_offer_pairs=token_offer_pairs
    )
    assert success == 2
    assert failure == 0


@patch("app.services.push_service.messaging")
async def test_batch_send_empty(
    mock_messaging: MagicMock,
    db_session: AsyncSession,
    test_job_with_offer: Job,
):
    """PS6: Batch with no token-offer pairs returns (0, 0)."""
    success, failure = await send_new_offer_batch(
        db_session, job=test_job_with_offer, token_offer_pairs=[]
    )
    assert success == 0
    assert failure == 0
