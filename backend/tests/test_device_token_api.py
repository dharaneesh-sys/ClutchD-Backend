"""Tests for the DeviceToken API endpoints.

Covers POST (register), DELETE (remove), and GET (list) endpoints.
Routes are at `/api/providers/device-tokens`.

DeviceTokenResponse schema returns: id, platform, is_active, created_at.
The token field is NOT exposed in responses.
"""

import os
import uuid

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-long-enough-for-pytest")

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.device_token import DeviceToken
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "/api/providers/device-tokens"


async def _auth_header(test_user: User) -> dict:
    token = create_access_token(subject=str(test_user.id))
    return {"Authorization": f"Bearer {token}"}


async def test_register_device_token(
    db_session: AsyncSession,
    client: AsyncClient,
    test_user: User,
):
    """DT-API1: Register a new device token returns 201 and token metadata."""
    headers = await _auth_header(test_user)
    payload = {
        "token": f"fcm-api-test-{uuid.uuid4().hex}",
        "platform": "android",
    }
    r = await client.post(BASE, json=payload, headers=headers)
    assert r.status_code == 201, r.text
    data = r.json()
    # Response schema: id, platform, is_active, created_at (no token field)
    assert data["platform"] == payload["platform"]
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


async def test_register_duplicate_token_same_user(
    db_session: AsyncSession,
    client: AsyncClient,
    test_user: User,
):
    """DT-API2: Same token from same user returns 201 (upsert/reactivates).

    The API treats same-user duplicates as upsert - it reactivates and
    updates the platform rather than rejecting with 409.
    """
    headers = await _auth_header(test_user)
    token_str = f"fcm-dup-api-{uuid.uuid4().hex}"
    payload = {"token": token_str, "platform": "ios"}

    r1 = await client.post(BASE, json=payload, headers=headers)
    assert r1.status_code == 201

    r2 = await client.post(BASE, json=payload, headers=headers)
    # Same user → upsert (201), not conflict (409)
    assert r2.status_code == 201, r2.text


async def test_delete_device_token(
    db_session: AsyncSession,
    client: AsyncClient,
    test_user: User,
):
    """DT-API3: Delete (deactivate) an existing token."""
    headers = await _auth_header(test_user)
    token_str = f"fcm-del-{uuid.uuid4().hex}"

    r = await client.post(
        BASE, json={"token": token_str, "platform": "android"}, headers=headers
    )
    assert r.status_code == 201
    token_id = r.json()["id"]

    r = await client.delete(f"{BASE}/{token_id}", headers=headers)
    assert r.status_code == 204, r.text

    r = await db_session.execute(
        select(DeviceToken).where(DeviceToken.id == uuid.UUID(token_id))
    )
    dt = r.scalar_one_or_none()
    assert dt is not None
    assert dt.is_active is False


async def test_delete_nonexistent_token(
    db_session: AsyncSession,
    client: AsyncClient,
    test_user: User,
):
    """DT-API4: Deleting a non-existent token returns 404."""
    headers = await _auth_header(test_user)
    r = await client.delete(f"{BASE}/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404, r.text


async def test_list_device_tokens(
    db_session: AsyncSession,
    client: AsyncClient,
    test_user: User,
):
    """DT-API5: List all active tokens for the authenticated user."""
    headers = await _auth_header(test_user)

    for i in range(2):
        payload = {
            "token": f"fcm-list-api-{i}-{uuid.uuid4().hex}",
            "platform": "android" if i % 2 == 0 else "ios",
        }
        r = await client.post(BASE, json=payload, headers=headers)
        assert r.status_code == 201

    r = await client.get(BASE, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2, data
    assert len(data["tokens"]) == 2, data


async def test_register_without_auth_returns_401(
    client: AsyncClient,
):
    """DT-API6: Unauthenticated request returns 401."""
    payload = {"token": "fcm-noauth", "platform": "android"}
    r = await client.post(BASE, json=payload)
    assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"
