"""Tests for seller product-creation endpoints.

Covers POST /api/products (and alias POST /api/marketplace/products),
GET /products/my-listings, PATCH/PUT and DELETE own-product rules.

Seller roles: mechanic | garage | admin. Images arrive as URLs returned by
POST /api/uploads (``/static/uploads/...``) or absolute http(s) URLs.
"""

import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-long-enough-for-pytest")

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.marketplace import MarketplaceCategory, MarketplaceProduct
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "/api/products"
ALIAS = "/api/marketplace/products"


def _headers(user: User) -> dict:
    token = create_access_token(subject=str(user.id))
    return {"Authorization": f"Bearer {token}"}


async def _make_user(db_session: AsyncSession, role: str, email: str, superuser: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash="$2b$12$abcdefghijklmnopqrstuvwx1234567890abcdefghijklmnopqrs",
        role=role,
        is_active=True,
        is_superuser=superuser,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _payload(**overrides) -> dict:
    base = {"name": "Brake Pad Set", "price": 1499.50, "brand": "Acme"}
    base.update(overrides)
    return base


async def test_create_product_201(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P1: mechanic creates a product -> 201 ProductResponse, vendor auto-provisioned."""
    user = test_mechanic.user
    r = await client.post(BASE, json=_payload(), headers=_headers(user))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "Brake Pad Set"
    assert abs(float(data["price"]) - 1499.50) < 0.01
    assert data["vendor"] == "Test Mechanic"  # auto-provisioned from mechanic profile
    assert data["vendor_id"] is not None
    assert "id" in data

    row = await db_session.execute(
        select(MarketplaceProduct).where(MarketplaceProduct.id == uuid.UUID(data["id"]))
    )
    product = row.scalar_one()
    assert product.seller_user_id == user.id


async def test_create_product_alias_201(
    db_session: AsyncSession, client: AsyncClient, test_garage
):
    """MP-P2: alias POST /api/marketplace/products works the same."""
    user = test_garage.user
    r = await client.post(ALIAS, json=_payload(name="Oil Filter"), headers=_headers(user))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "Oil Filter"
    assert data["vendor"] == "Test Garage"


async def test_create_product_403_customer(
    db_session: AsyncSession, client: AsyncClient, test_user: User
):
    """MP-P3: customer role cannot create products."""
    r = await client.post(BASE, json=_payload(), headers=_headers(test_user))
    assert r.status_code == 403, r.text


async def test_create_product_401_anon(db_session: AsyncSession, client: AsyncClient):
    """MP-P4: anonymous request is rejected with 401."""
    r = await client.post(BASE, json=_payload())
    assert r.status_code == 401, r.text


async def test_create_product_422_bad_price(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P5: negative price fails validation."""
    r = await client.post(BASE, json=_payload(price=-5), headers=_headers(test_mechanic.user))
    assert r.status_code == 422, r.text


async def test_create_product_422_blank_name(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P6: blank name fails validation."""
    r = await client.post(BASE, json=_payload(name="   "), headers=_headers(test_mechanic.user))
    assert r.status_code == 422, r.text


async def test_create_product_422_unknown_category(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P7: unknown category string is rejected."""
    r = await client.post(
        BASE, json=_payload(category="no-such-category"), headers=_headers(test_mechanic.user)
    )
    assert r.status_code == 422, r.text


async def test_create_product_category_slug_resolved(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P8: category slug resolves to category_id + denormalized name."""
    cat = MarketplaceCategory(id=uuid.uuid4(), slug="brakes", name="Brakes")
    db_session.add(cat)
    await db_session.commit()
    r = await client.post(
        BASE, json=_payload(category="brakes"), headers=_headers(test_mechanic.user)
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["category_id"] == str(cat.id)
    assert data["category"] == "Brakes"


async def test_my_listings_isolation(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P9: my-listings returns only the caller's own products."""
    me = test_mechanic.user
    other = await _make_user(db_session, "mechanic", f"other-{uuid.uuid4().hex}@ex.com")

    r1 = await client.post(BASE, json=_payload(name="Mine A"), headers=_headers(me))
    assert r1.status_code == 201, r1.text
    r2 = await client.post(BASE, json=_payload(name="Theirs B"), headers=_headers(other))
    assert r2.status_code == 201, r2.text

    mine = await client.get(f"{BASE}/my-listings", headers=_headers(me))
    assert mine.status_code == 200, mine.text
    names = [p["name"] for p in mine.json()["products"]]
    assert "Mine A" in names
    assert "Theirs B" not in names

    theirs = await client.get(f"{ALIAS}/my-listings", headers=_headers(other))
    assert theirs.status_code == 200, theirs.text
    other_names = [p["name"] for p in theirs.json()["products"]]
    assert "Theirs B" in other_names
    assert "Mine A" not in other_names


async def test_update_owner_vs_other_vs_admin(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P10: owner can PATCH, stranger gets 403, admin can PATCH."""
    me = test_mechanic.user
    other = await _make_user(db_session, "garage", f"other-{uuid.uuid4().hex}@ex.com")
    admin = await _make_user(db_session, "admin", f"admin-{uuid.uuid4().hex}@ex.com")

    created = await client.post(BASE, json=_payload(name="Wrench"), headers=_headers(me))
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    denied = await client.patch(f"{BASE}/{pid}", json={"price": 10}, headers=_headers(other))
    assert denied.status_code == 403, denied.text

    ok = await client.patch(f"{BASE}/{pid}", json={"price": 777.25}, headers=_headers(me))
    assert ok.status_code == 200, ok.text
    assert abs(float(ok.json()["price"]) - 777.25) < 0.01

    by_admin = await client.patch(f"{ALIAS}/{pid}", json={"price": 5}, headers=_headers(admin))
    assert by_admin.status_code == 200, by_admin.text


async def test_delete_owner_vs_other_vs_admin(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P11: owner can DELETE, stranger gets 403, admin can DELETE any."""
    me = test_mechanic.user
    other = await _make_user(db_session, "mechanic", f"other-{uuid.uuid4().hex}@ex.com")
    admin = await _make_user(db_session, "admin", f"admin-{uuid.uuid4().hex}@ex.com")

    created = await client.post(BASE, json=_payload(name="Deletable"), headers=_headers(me))
    pid = created.json()["id"]
    denied = await client.delete(f"{BASE}/{pid}", headers=_headers(other))
    assert denied.status_code == 403, denied.text
    gone = await client.delete(f"{BASE}/{pid}", headers=_headers(me))
    assert gone.status_code == 204, gone.text

    created2 = await client.post(BASE, json=_payload(name="Admin Deleted"), headers=_headers(me))
    pid2 = created2.json()["id"]
    by_admin = await client.delete(f"{ALIAS}/{pid2}", headers=_headers(admin))
    assert by_admin.status_code == 204, by_admin.text


async def test_update_delete_404_missing(
    db_session: AsyncSession, client: AsyncClient, test_mechanic
):
    """MP-P12: PATCH/DELETE on unknown id returns 404."""
    headers = _headers(test_mechanic.user)
    missing = str(uuid.uuid4())
    r1 = await client.patch(f"{BASE}/{missing}", json={"price": 1}, headers=headers)
    assert r1.status_code == 404, r1.text
    r2 = await client.delete(f"{BASE}/{missing}", headers=headers)
    assert r2.status_code == 404, r2.text
