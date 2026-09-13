"""Tests for the chat feature: history endpoint + participation rules."""

import pytest

pytestmark = pytest.mark.asyncio


async def _signup(client, email, role="customer"):
    await client.post("/api/auth/signup", json={
        "role": role,
        "email": email,
        "password": "TestPass123!",
        "fullName": "Chat Tester",
        "phone": "9633333333",
    })


async def _login(client, email):
    resp = await client.post("/api/auth/login", json={
        "email": email,
        "password": "TestPass123!",
    })
    return resp.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_chat_history_requires_auth(client):
    resp = await client.get("/api/chat/history/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


async def test_chat_history_job_not_found(client, db_session):
    await _signup(client, "chat-auth-test@example.com")
    token = await _login(client, "chat-auth-test@example.com")
    resp = await client.get(
        "/api/chat/history/00000000-0000-0000-0000-000000000000",
        headers=_auth(token),
    )
    assert resp.status_code == 404
