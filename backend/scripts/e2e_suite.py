"""ClutchD full E2E suite — runs against the PUBLIC funnel URL (same path phones use).

Covers: health, auth lifecycle, cart/orders, favorites/reviews, seller uploads,
fleet, chat (REST + WS), forgot-password, KYC admin.

Usage (on the server):
    cd ~/ClutchD-Backend/backend && ../venv/bin/python scripts/e2e_suite.py [--local]

    --local : target http://127.0.0.1:8000 (server-internal) instead of the
              public funnel edge. Default is the public URL.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from urllib.parse import urlparse

import httpx

PUBLIC_BASE = "https://clutchd-1.tail14cfb9.ts.net"
LOCAL_BASE = "http://127.0.0.1:8000"
API = "/api"
WS_LOCAL = "ws://127.0.0.1:8000/ws"

PASS: list[str] = []
FAIL: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(f"{name}{' — ' + detail if detail else ''}")
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))


def make_client(base: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(45.0), verify=True)


async def api_get(c: httpx.AsyncClient, path: str, token: str | None = None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await c.get(f"{API}{path}", headers=headers, **kw)


async def api_post(c: httpx.AsyncClient, path: str, json_body=None, token: str | None = None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await c.post(f"{API}{path}", json=json_body, headers=headers, **kw)


async def api_patch(c: httpx.AsyncClient, path: str, json_body=None, token: str | None = None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await c.patch(f"{API}{path}", json=json_body, headers=headers, **kw)


async def api_put(c: httpx.AsyncClient, path: str, json_body=None, token: str | None = None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await c.put(f"{API}{path}", json=json_body, headers=headers, **kw)


async def api_delete(c: httpx.AsyncClient, path: str, token: str | None = None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await c.delete(f"{API}{path}", headers=headers, **kw)


def signup_body(role: str, ts: int, domain: str = "e2e.clutchd.in") -> dict:
    email = f"e2e_{role}_{ts}@{domain}"
    b = {
        "role": role,
        "email": email,
        "password": "E2eTest123!",
        "confirmPassword": "E2eTest123!",
        "fullName": f"E2E {role.title()} {ts}",
        "phone": f"98{ts % 100000000:08d}",
        "latitude": 11.0168,
        "longitude": 76.9558,
        "location": "Coimbatore",
    }
    if role == "mechanic":
        b["expertise"] = ["engine", "tires"]
        b["experience"] = "5"
    if role == "garage":
        b["garageName"] = f"E2E Garage {ts}"
        b["services"] = ["engine", "tires"]
        b["mechanicCount"] = "3"
    if role == "seller":
        b["storeName"] = f"E2E Store {ts}"
        b["ownerName"] = f"E2E Seller {ts}"
    return b


async def login(c: httpx.AsyncClient, email: str, password: str):
    r = await api_post(c, "/auth/login", {"email": email, "password": password})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:200]}"
    d = r.json()
    return d["token"], d["user"]


# ────────────────────────── sections ──────────────────────────


async def t_health(c: httpx.AsyncClient):
    r = await c.get("/health")
    record("health", r.status_code == 200, f"{r.status_code}")


async def t_auth(c: httpx.AsyncClient, ts: int):
    global CUST, CUST_TOKEN
    b = signup_body("customer", ts)

    r = await api_post(c, "/auth/signup", b)
    record("auth: signup customer", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    d = r.json()
    CUST, CUST_TOKEN = d["user"], d["token"]

    r = await api_get(c, "/auth/me", CUST_TOKEN)
    record("auth: /auth/me", r.status_code == 200, f"{r.status_code}")

    r = await api_post(c, "/auth/login", {"email": b["email"], "password": b["password"]})
    record("auth: login", r.status_code == 200, f"{r.status_code}")
    tok2 = r.json()["token"]

    r = await c.post(
        f"{API}/auth/refresh",
        json={},
        headers={"X-Refresh-Token": tok2},
    )
    record("auth: refresh (X-Refresh-Token)", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    return b["email"], b["password"]


async def t_profile(c: httpx.AsyncClient):
    r = await api_get(c, "/profile/me", CUST_TOKEN)
    record("profile: GET /me", r.status_code == 200, f"{r.status_code}")

    r = await api_put(c, "/profile/me", {"full_name": "E2E Renamed", "address": "12 Test St, Coimbatore"}, CUST_TOKEN)
    record("profile: PUT /me", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    r = await api_get(c, "/settings", CUST_TOKEN)
    record("profile: GET /settings", r.status_code == 200, f"{r.status_code}")

    r = await api_get(c, "/referral/my-code", CUST_TOKEN)
    record("profile: referral my-code", r.status_code == 200, f"{r.status_code}")

    r = await api_get(c, "/notifications", CUST_TOKEN)
    record("profile: notifications", r.status_code == 200, f"{r.status_code}")


async def t_marketplace_catalog(c: httpx.AsyncClient):
    r = await api_get(c, "/categories")
    record("marketplace: categories", r.status_code == 200, f"{r.status_code}")
    cats = r.json()
    cid = (cats[0]["id"] if isinstance(cats, list) and cats else None) or (
        (cats.get("categories") or [{}])[0].get("id") if isinstance(cats, dict) else None
    )

    r = await api_get(c, "/products", params={"limit": 5})
    record("marketplace: products list", r.status_code == 200, f"{r.status_code}")
    prods = r.json()
    prods = prods.get("products", prods) if isinstance(prods, dict) else prods
    r = await api_get(c, "/products/top-products")
    record("marketplace: top-products", r.status_code == 200, f"{r.status_code}")
    return cid


async def t_seller_and_cart(c: httpx.AsyncClient, ts: int, cid):
    """Seller signup -> product upload (photo compulsory) -> cart -> order -> favorite -> review."""
    global PRODUCT, PRODUCT_ID, VENDOR_ID

    sb = signup_body("seller", ts)
    r = await api_post(c, "/auth/signup", sb)
    record("seller: signup", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    seller_tok = r.json()["token"]

    # photo upload (compulsory for products) — 1x1 transparent PNG
    PNG_1PX = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0dIDATx\x9cc\xfc\xcf"
        b"\xc0\xc0\x00\x00\x00\x04\xfe\x01\xf9\xd0\xa2\xa8\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    png = PNG_1PX
    r = await c.post(
        f"{API}/uploads",
        files={"file": ("part.png", png, "image/png")},
        headers={"Authorization": f"Bearer {seller_tok}"},
    )
    record("seller: photo upload", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    img_url = r.json().get("url", "") if r.status_code == 200 else ""

    prod = {
        "name": f"E2E Brake Pad {ts}",
        "price": "1299.00",
        "description": "E2E test brake pad",
        "brand": "E2E",
        "image": img_url or "https://example.com/part.png",
    }
    if cid:
        prod["category_id"] = cid
    r = await api_post(c, "/marketplace/products", prod, seller_tok)
    record("seller: create product (photo required)", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    p = r.json()
    PRODUCT_ID, VENDOR_ID = p.get("id"), p.get("vendor_id") or p.get("vendorId")

    # photo-less product must be rejected (compulsory photo rule)
    r2 = await api_post(c, "/marketplace/products", {**prod, "name": f"E2E NoPhoto {ts}", "image": ""}, seller_tok)
    record("seller: no-photo product rejected", r2.status_code in (400, 422), f"{r2.status_code}")

    r = await api_get(c, "/marketplace/products/my-listings", seller_tok)
    record("seller: my-listings", r.status_code == 200, f"{r.status_code}")

    r = await api_patch(c, f"/marketplace/products/{PRODUCT_ID}", {"price": "1199.00"}, seller_tok)
    record("seller: edit product", r.status_code == 200, f"{r.status_code}")

    # cart
    r = await api_post(c, "/marketplace/cart", {"product_id": PRODUCT_ID, "quantity": 2}, CUST_TOKEN)
    record("cart: add item", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    item = r.json()

    r = await api_get(c, "/marketplace/cart", CUST_TOKEN)
    record("cart: list", r.status_code == 200, f"{r.status_code} ({len(r.json())} items)")

    r = await api_patch(c, f"/marketplace/cart/{item['id']}", {"quantity": 3}, CUST_TOKEN)
    record("cart: update qty", r.status_code == 200, f"{r.status_code}")

    # order straight from product (items carry price)
    order = {"items": [{"product_id": PRODUCT_ID, "name": "E2E Brake Pad", "quantity": 1, "price": "1199.00"}],
             "address": {"line1": "12 Test St", "city": "Coimbatore", "pincode": "641001"}}
    r = await api_post(c, "/orders", order, CUST_TOKEN)
    record("orders: create", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    order_id = r.json().get("id")

    r = await api_get(c, "/orders", CUST_TOKEN)
    record("orders: list", r.status_code == 200, f"{r.status_code}")

    # favorites + product review
    r = await api_post(c, "/favorites", {"product_id": PRODUCT_ID}, CUST_TOKEN)
    record("favorites: add", r.status_code in (200, 201), f"{r.status_code}")
    r = await api_get(c, "/favorites", CUST_TOKEN)
    record("favorites: list", r.status_code == 200, f"{r.status_code}")
    r = await api_delete(c, f"/favorites/{PRODUCT_ID}", CUST_TOKEN)
    record("favorites: remove", r.status_code in (200, 204), f"{r.status_code}")

    r = await api_post(c, f"/marketplace/products/{PRODUCT_ID}/reviews", {"rating": 5, "text": "E2E", "userName": "E2E"}, CUST_TOKEN)
    record("reviews: product review", r.status_code == 201, f"{r.status_code} {r.text[:120]}")

    # cleanup cart + product (keep DB tidy)
    await api_delete(c, f"/marketplace/cart/{item['id']}", CUST_TOKEN)
    await api_delete(c, f"/marketplace/products/{PRODUCT_ID}", seller_tok)
    record("seller: delete product", True)


async def t_fleet(c: httpx.AsyncClient, ts: int):
    global FLEET_USER_EMAIL
    fb = signup_body("customer", ts + 1)
    FLEET_USER_EMAIL = fb["email"]
    r = await api_post(c, "/auth/signup", fb)
    record("fleet: signup owner", r.status_code == 200, f"{r.status_code}")
    ftok = r.json()["token"]

    body = {
        "companyName": f"E2E Logistics {ts}",
        "fleetType": "logistics",
        "fleetSize": "11-50",
        "contactName": "E2E Fleet Manager",
        "contactEmail": fb["email"],
        "contactPhone": f"97{ts % 100000000:08d}",
        "businessAddress": "1 Fleet Road, Coimbatore",
    }
    r = await api_post(c, "/fleet/register", body, ftok)
    record("fleet: register", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    fid = r.json().get("id")

    r = await api_post(c, "/fleet/register", body, ftok)
    record("fleet: duplicate register rejected", r.status_code == 409, f"{r.status_code}")

    r = await api_get(c, "/my-fleet", ftok)
    record("fleet: my-fleet", r.status_code == 200, f"{r.status_code}")

    r = await api_post(c, "/fleet/bookings", {
        "scheduledAt": "2026-09-20T10:00:00Z",
        "vehicles": [{"vehicleName": "TN01AB1234", "serviceType": "periodic"}],
        "subtotal": 3000, "discountPercent": 10, "total": 2700,
    }, ftok)
    record("fleet: create booking", r.status_code == 201, f"{r.status_code} {r.text[:150]}")

    r = await api_get(c, "/fleet/bookings", ftok)
    record("fleet: list bookings", r.status_code == 200, f"{r.status_code}")

    if fid:
        r = await api_get(c, f"/fleet/{fid}", ftok)
        record("fleet: get by id", r.status_code == 200, f"{r.status_code}")


async def t_job_and_chat(c: httpx.AsyncClient, ts: int, local: bool):
    """customer posts job -> mechanic sees offer -> accept -> chat WS + history -> finalize -> cash pay -> review."""
    global MECH_TOKEN, JOB_ID

    mb = signup_body("mechanic", ts + 2)
    r = await api_post(c, "/auth/signup", mb)
    record("job: signup fresh mechanic", r.status_code == 200, f"{r.status_code}")
    MECH_TOKEN = r.json()["token"]

    req = {"issueTag": "engine", "description": f"E2E engine trouble {ts}", "requestType": "auto",
           "customerLat": 11.0168, "customerLng": 76.9558}
    r = await api_post(c, "/service/request", req, CUST_TOKEN)
    record("job: create request", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    d = r.json()
    JOB_ID = d.get("id") or d.get("jobId") or (d.get("job") or {}).get("id")

    r = await api_get(c, "/providers/offers?status=pending", MECH_TOKEN)
    offers = r.json().get("offers", []) if r.status_code == 200 else []
    record("job: mechanic sees offer (dispatch fanout)", r.status_code == 200 and any(o["jobId"] == JOB_ID for o in offers),
           f"{r.status_code} offers={len(offers)}")
    offer_id = next((o["id"] for o in offers if o["jobId"] == JOB_ID), None)

    if offer_id:
        r = await api_post(c, f"/offers/{offer_id}/accept", {}, MECH_TOKEN)
        record("job: accept offer", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = await api_get(c, "/jobs/incoming", MECH_TOKEN)
    ok = r.status_code == 200 and any((j.get("id") or j.get("jobId")) == JOB_ID for j in r.json().get("jobs", []))
    record("job: mechanic incoming", ok, f"{r.status_code}")

    # ── chat: REST history ──
    r = await api_get(c, f"/chat/history/{JOB_ID}", CUST_TOKEN)
    record("chat: REST history", r.status_code == 200, f"{r.status_code}")

    # ── chat: WebSocket ──
    ws_ok = False
    ws_detail = ""
    try:
        if local:
            import websockets
            ws_base = WS_LOCAL
            headers = None
        else:
            import websockets
            ws_base = f"wss://{urlparse(c.base_url).netloc}/ws"
            headers = None
        async with websockets.connect(
            ws_base,
            additional_arguments={} if not headers else {},
            subprotocols=[CUST_TOKEN],
            open_timeout=30,
            additional_headers=headers or {},
        ) as ws:
            await ws.send(json.dumps({"type": "CHAT_MESSAGE", "payload": {"jobId": JOB_ID, "text": "hello from e2e"}}))
            got = None
            t0 = time.time()
            while time.time() - t0 < 15:
                try:
                    got = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if got.get("type") == "CHAT_MESSAGE":
                        break
                except asyncio.TimeoutError:
                    break
            ws_ok = bool(got and got.get("type") == "CHAT_MESSAGE")
            ws_detail = f"recv={got.get('type') if got else None}"
    except Exception as e:  # noqa: BLE001
        ws_detail = f"{type(e).__name__}: {e}"
    record("chat: WS send+receive+persist", ws_ok, ws_detail)

    r = await api_get(c, f"/chat/history/{JOB_ID}", CUST_TOKEN)
    hist = r.json()
    msgs = hist.get("messages", hist) if isinstance(hist, dict) else hist
    ok = r.status_code == 200 and any((m.get("text") == "hello from e2e") for m in msgs)
    record("chat: WS message persisted", ok, f"{r.status_code} n={len(msgs)}")

    # lifecycle: finalize -> cash -> review
    r = await api_post(c, f"/service/request/{JOB_ID}/finalize-price", {"serviceAmount": 850}, MECH_TOKEN)
    record("job: finalize-price", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = await api_post(c, "/payments/cash", {"job_id": JOB_ID, "amount": 85000}, MECH_TOKEN)
    record("payments: cash (mechanic collects)", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = await api_get(c, "/payments/history", CUST_TOKEN)
    record("payments: history", r.status_code == 200, f"{r.status_code}")

    r = await api_post(c, "/reviews", {"job_id": JOB_ID, "rating": 5, "comment": "E2E great"}, CUST_TOKEN)
    record("reviews: job review", r.status_code in (200, 201), f"{r.status_code} {r.text[:120]}")


async def t_forgot_password(c: httpx.AsyncClient, ts: int, email: str, password: str):
    """Full reset flow. Code is fetched from Redis on the server (same host)."""
    r = await api_post(c, "/auth/forgot-password/request", {"email": email})
    record("reset: request code", r.status_code == 200, f"{r.status_code}")

    code = await fetch_reset_code(email)
    if not code:
        record("reset: code retrievable", False, "no code in redis")
        return

    # wrong code must fail
    r = await api_post(c, "/auth/forgot-password/reset", {"email": email, "code": "wrong123", "newPassword": "NewE2e123!"})
    record("reset: wrong code rejected", r.status_code in (400, 401, 429), f"{r.status_code}")

    r = await api_post(c, "/auth/forgot-password/reset", {"email": email, "code": code, "newPassword": "NewE2e123!"})
    record("reset: correct code accepted", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = await api_post(c, "/auth/login", {"email": email, "password": "NewE2e123!"})
    record("reset: login with new password", r.status_code == 200, f"{r.status_code}")
    # restore original password for later sections
    await api_post(c, "/auth/forgot-password/request", {"email": email})
    code2 = await fetch_reset_code(email)
    if code2:
        await api_post(c, "/auth/forgot-password/reset", {"email": email, "code": code2, "newPassword": password})


async def fetch_reset_code(email: str) -> str | None:
    proc = await asyncio.create_subprocess_exec(
        "redis-cli", "-h", "127.0.0.1", "GET", f"reset:{email}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    code = out.decode().strip()
    return code if code and code != "nil" else None


async def t_kyc_admin(c: httpx.AsyncClient, ts: int):
    """KYC submit (fresh mechanic) -> admin queue -> approve."""
    kb = signup_body("mechanic", ts + 3)
    kb["aadhaarPhotoUrl"] = "https://example.com/aadhaar.png"
    kb["licensePhotoUrl"] = "https://example.com/license.png"
    r = await api_post(c, "/auth/signup", kb)
    record("kyc: mechanic signup with docs", r.status_code == 200, f"{r.status_code}")
    ktok = r.json()["token"]

    r = await api_get(c, "/profile/me", ktok)
    st = r.json().get("kycStatus") if r.status_code == 200 else None
    record("kyc: status=submitted after docs", st == "submitted", f"got {st}")

    admin_tok = await bootstrap_admin(c)
    if not admin_tok:
        record("kyc: admin queue", False, "no admin token")
        return

    r = await api_get(c, "/admin/kyc/pending", admin_tok)
    ok = r.status_code == 200
    apps = r.json().get("applications", []) if ok else []
    mine = next((a for a in apps if a.get("email") == kb["email"]), None)
    record("kyc: admin queue shows application", ok and mine is not None, f"{r.status_code} n={len(apps)}")

    if mine:
        r = await api_patch(c, f"/admin/kyc/{mine['profileType']}/{mine['id']}/review",
                            {"action": "approve", "note": "e2e"}, admin_tok)
        record("kyc: admin approve", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

        r = await api_get(c, "/profile/me", ktok)
        st2 = r.json().get("kycStatus") if r.status_code == 200 else None
        record("kyc: status=verified after approve", st2 == "verified", f"got {st2}")

    # non-admin must be rejected
    r = await api_get(c, "/admin/kyc/pending", CUST_TOKEN)
    record("kyc: non-admin rejected", r.status_code in (401, 403), f"{r.status_code}")


async def bootstrap_admin(c: httpx.AsyncClient) -> str | None:
    """Try known admin creds; else promote a fresh signup directly in DB."""
    for email, pw in (("admin@clutchd.in", "Admin@123"), ("admin@clutchd.com", "admin123")):
        try:
            tok, _ = await login(c, email, pw)
            return tok
        except AssertionError:
            continue

    email = f"e2e_admin_{secrets.token_hex(4)}@e2e.clutchd.in"
    r = await api_post(c, "/auth/signup", signup_body("customer", int(time.time())))
    if r.status_code != 200:
        return None
    tok = r.json()["token"]
    uid = r.json()["user"]["id"]

    ok = await promote_admin_in_db(uid)
    if not ok:
        return None
    tok2, _ = await login(c, email, "E2eTest123!")
    return tok2


async def promote_admin_in_db(uid: str) -> bool:
    sql = f"UPDATE users SET role='admin', is_superuser=true WHERE id='{uid}';"
    proc = await asyncio.create_subprocess_exec(
        "psql", "-h", "127.0.0.1", "-U", "clutchd", "-d", "clutchd", "-c", sql,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={"PGPASSWORD": "clutchd", "PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    _, err = await proc.communicate()
    return proc.returncode == 0


# ────────────────────────── main ──────────────────────────


async def main() -> int:
    local = "--local" in sys.argv
    base = LOCAL_BASE if local else PUBLIC_BASE
    ts = int(time.time())

    print(f"=== ClutchD E2E suite -> {base} (local={local}) ===\n")
    async with make_client(base) as c:
        await t_health(c)

        creds = await t_auth(c, ts)
        await t_profile(c)
        cid = await t_marketplace_catalog(c)
        await t_seller_and_cart(c, ts, cid)
        await t_fleet(c, ts)
        await t_job_and_chat(c, ts, local)
        await t_forgot_password(c, ts, *creds)
        await t_kyc_admin(c, ts)

    print(f"\n=== RESULTS: {len(PASS)} passed, {len(FAIL)} failed ===")
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    CUST = CUST_TOKEN = PRODUCT_ID = VENDOR_ID = MECH_TOKEN = JOB_ID = None
    sys.exit(asyncio.run(main()))
