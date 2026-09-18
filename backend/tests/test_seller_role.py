"""Tests for the seller role: signup, payload, and marketplace permissions."""
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.seller import Seller
from app.models.user import User
from app.core.security import create_access_token


pytestmark = pytest.mark.asyncio


async def _headers_for(db: AsyncSession, email: str) -> dict:
    r = await db.execute(select(User).where(User.email == email))
    user = r.scalar_one_or_none()
    assert user is not None, f"user {email} must exist"
    return {"Authorization": f"Bearer {create_access_token(str(user.id), {'role': user.role})}"}


async def test_seller_signup_creates_user_and_profile(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post("/api/auth/signup", json={
        "role": "seller",
        "email": "seller-role-test@example.com",
        "password": "TestPass123!",
        "storeName": "Test Parts Bazaar",
        "ownerName": "Sell Bot",
        "phone": "9876543299",
        "location": "Kochi",
    })
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["user"]["role"] == "seller"
    assert body["user"]["name"] == "Test Parts Bazaar"

    r = await db_session.execute(select(Seller).where(Seller.store_name == "Test Parts Bazaar"))
    seller = r.scalar_one_or_none()
    assert seller is not None
    assert seller.phone == "9876543299"


async def test_seller_can_create_product(client: AsyncClient, db_session: AsyncSession):
    await client.post("/api/auth/signup", json={
        "role": "seller",
        "email": "seller-product-test@example.com",
        "password": "TestPass123!",
        "storeName": "Product Parts Co",
    })
    headers = await _headers_for(db_session, "seller-product-test@example.com")

    resp = await client.post("/api/marketplace/products", headers=headers, json={
        "name": "Test Brake Pad Set",
        "price": 1249,
        "description": "Front pads",
        "image": "/static/uploads/test-brake-pad.png",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["vendor"] == "Product Parts Co"
    assert data["image"] == "/static/uploads/test-brake-pad.png"


async def test_customer_cannot_create_product(client: AsyncClient, db_session: AsyncSession):
    await client.post("/api/auth/signup", json={
        "role": "customer",
        "email": "cust-no-sell@example.com",
        "password": "TestPass123!",
    })
    headers = await _headers_for(db_session, "cust-no-sell@example.com")

    resp = await client.post("/api/marketplace/products", headers=headers, json={
        "name": "Should Fail Part",
        "price": 100,
    })
    assert resp.status_code == 403


async def test_seller_my_listings(client: AsyncClient, db_session: AsyncSession):
    await client.post("/api/auth/signup", json={
        "role": "seller",
        "email": "seller-listings-test@example.com",
        "password": "TestPass123!",
        "storeName": "Listings Bazaar",
    })
    headers = await _headers_for(db_session, "seller-listings-test@example.com")

    created = await client.post("/api/marketplace/products", headers=headers, json={
        "name": "My Listing Part",
        "price": 500,
        "image": "/static/uploads/my-listing-part.png",
    })
    assert created.status_code == 201, created.text

    resp = await client.get("/api/marketplace/products/my-listings", headers=headers)
    assert resp.status_code == 200, resp.text
    names = [p["name"] for p in resp.json().get("products", [])]
    assert "My Listing Part" in names


async def test_create_product_without_image_rejected(client: AsyncClient, db_session: AsyncSession):
    """Part photo is compulsory: creating a listing without an image 422s."""
    await client.post("/api/auth/signup", json={
        "role": "seller",
        "email": "seller-noimg-test@example.com",
        "password": "TestPass123!",
        "storeName": "No Image Co",
    })
    headers = await _headers_for(db_session, "seller-noimg-test@example.com")

    resp = await client.post("/api/marketplace/products", headers=headers, json={
        "name": "No Photo Part",
        "price": 300,
    })
    assert resp.status_code == 422, resp.text

    resp = await client.post("/api/marketplace/products", headers=headers, json={
        "name": "Blank Photo Part",
        "price": 300,
        "image": "   ",
    })
    assert resp.status_code == 422, resp.text


async def test_seller_login_returns_store_name(client: AsyncClient, db_session: AsyncSession):
    await client.post("/api/auth/signup", json={
        "role": "seller",
        "email": "seller-login-test@example.com",
        "password": "TestPass123!",
        "storeName": "Login Bazaar",
    })
    resp = await client.post("/api/auth/login", json={
        "email": "seller-login-test@example.com",
        "password": "TestPass123!",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["role"] == "seller"
    assert resp.json()["user"]["name"] == "Login Bazaar"


async def test_seller_sales_endpoint(client: AsyncClient, db_session: AsyncSession):
    """GET /orders/sales returns only orders containing the caller's products."""
    await client.post("/api/auth/signup", json={
        "role": "seller",
        "email": "seller-sales-test@example.com",
        "password": "TestPass123!",
        "storeName": "Sales Bazaar",
    })
    headers = await _headers_for(db_session, "seller-sales-test@example.com")

    created = await client.post("/api/marketplace/products", headers=headers, json={
        "name": "Sales Test Clutch Plate",
        "price": 2100,
        "image": "/static/uploads/sales-test.png",
    })
    assert created.status_code == 201, created.text
    product_id = created.json()["id"]

    # Customer buys the seller's product (order is placed as the buyer).
    await client.post("/api/auth/signup", json={
        "role": "customer",
        "email": "sales-buyer-test@example.com",
        "password": "TestPass123!",
        "fullName": "Sales Buyer",
        "phone": "9611111101",
    })
    buyer_headers = await _headers_for(db_session, "sales-buyer-test@example.com")
    order = await client.post("/api/orders", headers=buyer_headers, json={
        "items": [
            {"product_id": product_id, "name": "Sales Test Clutch Plate", "quantity": 2, "price": 2100},
        ],
        "address": {"line1": "1 Test St", "city": "Kochi"},
    })
    assert order.status_code == 201, order.text

    # Seller sees the sale with their line items.
    resp = await client.get("/api/orders/sales", headers=headers)
    assert resp.status_code == 200, resp.text
    sales = resp.json()["orders"]
    assert len(sales) == 1, sales
    assert sales[0]["items"][0]["name"] == "Sales Test Clutch Plate"
    assert Decimal(str(sales[0]["total"])) == Decimal("4200.00")

    # A customer/stranger calling the sales endpoint is rejected (seller-only).
    forbidden = await client.get("/api/orders/sales", headers=buyer_headers)
    assert forbidden.status_code in (403, 401), forbidden.text


async def test_login_unknown_email_returns_404(client: AsyncClient, db_session: AsyncSession):
    """Unknown email → 404 (sign-up hint), wrong password on real account → 401."""
    await client.post("/api/auth/signup", json={
        "role": "customer",
        "email": "login-404-test@example.com",
        "password": "TestPass123!",
        "fullName": "Login 404",
        "phone": "9611111102",
    })
    missing = await client.post("/api/auth/login", json={
        "email": "ghost-who-doesnt-exist@example.com",
        "password": "Whatever123!",
    })
    assert missing.status_code == 404, missing.text

    wrongpw = await client.post("/api/auth/login", json={
        "email": "login-404-test@example.com",
        "password": "NotThePassword1!",
    })
    assert wrongpw.status_code == 401, wrongpw.text
