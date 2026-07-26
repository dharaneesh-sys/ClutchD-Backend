"""Tests for the DeviceToken ORM model.

These tests cover creation, constraints, cascading deactivation,
unique constraint enforcement, and token listing by user.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_token import DeviceToken
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def test_create_device_token(db_session: AsyncSession, test_user: User):
    """DT1: Create a device token and verify its fields."""
    token_str = f"fcm-token-{uuid.uuid4().hex}"
    dt = DeviceToken(
        id=uuid.uuid4(),
        user_id=test_user.id,
        token=token_str,
        platform="android",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(dt)
    await db_session.commit()
    await db_session.refresh(dt)

    assert dt.token == token_str
    assert dt.platform == "android"
    assert dt.is_active is True
    assert dt.user_id == test_user.id


async def test_deactivate_token(db_session: AsyncSession, test_user: User):
    """DT2: Deactivate a token and confirm is_active flips."""
    dt = DeviceToken(
        id=uuid.uuid4(),
        user_id=test_user.id,
        token=f"fcm-{uuid.uuid4().hex}",
        platform="ios",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(dt)
    await db_session.commit()

    dt.is_active = False
    await db_session.commit()
    await db_session.refresh(dt)
    assert dt.is_active is False


async def test_unique_token_per_user(db_session: AsyncSession, test_user: User):
    """DT3: The same token cannot be registered twice for the same user."""
    token_str = f"dup-{uuid.uuid4().hex}"
    dt1 = DeviceToken(
        id=uuid.uuid4(),
        user_id=test_user.id,
        token=token_str,
        platform="android",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(dt1)
    await db_session.commit()

    dt2 = DeviceToken(
        id=uuid.uuid4(),
        user_id=test_user.id,
        token=token_str,
        platform="android",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(dt2)
    with pytest.raises(Exception):
        await db_session.commit()


async def test_list_tokens_for_user(db_session: AsyncSession, test_user: User):
    """DT4: Query device tokens for a user returns expected count."""
    for i in range(3):
        dt = DeviceToken(
            id=uuid.uuid4(),
            user_id=test_user.id,
            token=f"fcm-list-{i}-{uuid.uuid4().hex}",
            platform="android" if i % 2 == 0 else "ios",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(dt)
    await db_session.commit()

    r = await db_session.execute(
        select(DeviceToken).where(
            DeviceToken.user_id == test_user.id,
            DeviceToken.is_active == True,
        )
    )
    tokens = r.scalars().all()
    assert len(tokens) == 3
    for t in tokens:
        assert t.user_id == test_user.id
