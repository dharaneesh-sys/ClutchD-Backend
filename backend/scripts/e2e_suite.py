"""ClutchD full E2E suite — exercises every feature against a running backend.

Covers: health, auth lifecycle, profile, marketplace catalog, seller uploads,
cart/orders, favorites/reviews, fleet, job lifecycle + chat (WS + history),
payments, forgot-password (full reset via Redis), KYC admin review.

The suite CREATES its own users/products and DELETES them at the end, so it
never leaves fake data in production.

Usage (on the server):
    cd ~/ClutchD-Backend/backend && ../venv/bin/python scripts/e2e_suite.py [--local]

    --local : target http://127.0.0.1:8000 (default; the public funnel edge
              cannot be reached from the server itself — DNS short-circuits).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from urllib.parse import urlparse

import httpx

PUBLIC_BASE = "https://clutchd-1.tail14cfb9.ts.net"
LOCAL_BASE = "http://127.0.0.1:8000"
API = "/api"
DOMAIN = "e2e.clutchd.in"  # all suite users use this domain → easy bulk cleanup

PASS: list[str] = []
FAIL: list[str] = []

# everything the suite created (for end-of-run cleanup)
CREATED_USER_IDS: set[str] = set()
CREATED_PRODUCT_IDS: list[str] = set()


def record(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(f"{name}{' — ' + detail if detail else ''}")
    tag = "PASS" if ok else "FAIL"
    det = f"  [{detail}]" if (detail and not ok) else ""
    print(f"{tag}  {name}{det}", flush=True)


def make_client(base: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(45.0))


async def req(c: httpx.AsyncClient, method: str, path: str, token: str | None = None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await c.request(method, f"{API}{path}", headers=headers, **kw)


async def api_get(c, path, token=None, **kw):
    return await req(c, "GET", path, token, **kw)


async def api_post(c, path, json_body=None, token=None, **kw):
    return await req(c, "POST", path, token, json=json_body, **kw)


async def api_patch(c, path, json_body=None, token=None, **kw):
    return await req(c, "PATCH", path, token, json=json_body, **kw)


async def api_put(c, path, json_body=None, token=None, **kw):
    return await req(c, "PUT", path, token, json=json_body, **kw)


async def api_delete(c, path, token=None, **kw):
    return await req(c, "DELETE", path, token, **kw)


def signup_body(role: str, ts: int) -> dict:
    b = {
        "role": role,
        "email": f"e2e_{role}_{ts}@{DOMAIN}",
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
    if role == "seller":
        b["storeName"] = f"E2E Store {ts}"
        b["ownerName"] = b["fullName"]
    return b


async def signup(c: httpx.AsyncClient, role: str, ts: int, **extra):
    body = {**signup_body(role, ts), **extra}
    r = await api_post(c, "/auth/signup", body)
    assert r.status_code == 200, f"signup {role}: {r.status_code} {r.text[:200]}"
    d = r.json()
    uid = d.get("user", {}).get("id")
    if uid:
        CREATED_USER_IDS.add(uid)
    return d  # {token, user, refresh_token}


async def login(c: httpx.AsyncClient, email: str, password: str) -> dict:
    r = await api_post(c, "/auth/login", {"email": email, "password": password})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:200]}"
    return r.json()


# ────────────────────────── sections ──────────────────────────


async def t_health(c: httpx.AsyncClient):
    r = await c.get("/health")
    record("health", r.status_code == 200, f"{r.status_code}")


async def t_auth(c: httpx.AsyncClient, ts: int) -> dict:
    """Signup → refresh → login. Returns creds of the suite customer."""
    b = signup_body("customer", ts)
    d = await signup(c, "customer", ts)
    cust_tok = d["token"]

    r = await api_post(c, "/auth/login", {"email": b["email"], "password": b["password"]})
    record("auth: login", r.status_code == 200, f"{r.status_code}")
    d2 = r.json()

    r = await c.post(f"{API}/auth/refresh", json={}, headers={"X-Refresh-Token": d2.get("refresh_token", "")})
    record("auth: refresh (X-Refresh-Token)", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    r = await api_get(c, "/profile/me", cust_tok)
    record("auth: session works (/profile/me)", r.status_code == 200, f"{r.status_code}")

    return {"email": b["email"], "password": b["password"], "token": cust_tok, "user": d["user"]}


async def t_profile(c: httpx.AsyncClient, tok: str):
    r = await api_get(c, "/profile/me", tok)
    record("profile: GET /me", r.status_code == 200, f"{r.status_code}")

    r = await api_put(c, "/profile/me", {"full_name": "E2E Renamed", "address": "12 Test St, Coimbatore"}, tok)
    record("profile: PUT /me", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    for name, path in (("settings", "/settings"), ("referral my-code", "/referral/my-code"),
                       ("notifications", "/notifications")):
        r = await api_get(c, path, tok)
        record(f"profile: GET {path}", r.status_code == 200, f"{r.status_code}")


async def t_marketplace_catalog(c: httpx.AsyncClient):
    r = await api_get(c, "/categories")
    record("marketplace: categories", r.status_code == 200, f"{r.status_code}")

    r = await api_get(c, "/products", params={"limit": 5})
    record("marketplace: products list", r.status_code == 200, f"{r.status_code}")

    r = await api_get(c, "/products/top-products")
    record("marketplace: top-products", r.status_code == 200, f"{r.status_code}")


async def t_seller_and_cart(c: httpx.AsyncClient, ts: int):
    """Seller signup → photo upload → product (photo compulsory) → cart → order → favorites → review."""
    seller = await signup(c, "seller", ts)
    stok = seller["token"]

    # 1x1 transparent PNG — photo upload is compulsory for products
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0dIDATx\x9cc\xfc\xcf"
        b"\xc0\xc0\x00\x00\x00\x04\xfe\x01\xf9\xd0\xa2\xa8\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    r = await c.post(f"{API}/uploads", files={"file": ("part.png", png, "image/png")},
                     headers={"Authorization": f"Bearer {stok}"})
    record("seller: photo upload", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    img_url = r.json().get("url", "") if r.status_code == 200 else ""

    prod = {"name": f"E2E Brake Pad {ts}", "price": "1299.00", "description": "E2E test brake pad",
            "brand": "E2E", "category": "brake-parts", "image": img_url or f"https://{DOMAIN}/part.png"}
    r = await api_post(c, "/marketplace/products", prod, stok)
    ok = r.status_code == 201
    record("seller: create product (with photo)", ok, f"{r.status_code} {r.text[:150]}")
    pid = r.json().get("id") if ok else None
    if pid:
        CREATED_PRODUCT_IDS.add(pid)

    r2 = await api_post(c, "/marketplace/products", {**prod, "name": f"E2E NoPhoto {ts}", "image": ""}, stok)
    record("seller: no-photo product rejected", r2.status_code in (400, 422), f"{r2.status_code}")

    r = await api_get(c, "/marketplace/products/my-listings", stok)
    record("seller: my-listings", r.status_code == 200, f"{r.status_code}")

    r = await api_patch(c, f"/marketplace/products/{pid}", {"price": "1199.00"}, stok)
    record("seller: edit product", r.status_code == 200, f"{r.status_code}")

    # ── cart (customer) ──
    cust = await signup(c, "customer", ts + 50)
    ctok = cust["token"]

    r = await api_post(c, "/marketplace/cart", {"product_id": pid, "quantity": 2}, ctok)
    record("cart: add item", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    item_id = r.json().get("id") if r.status_code == 201 else None

    r = await api_get(c, "/marketplace/cart", ctok)
    record("cart: list", r.status_code == 200, f"{r.status_code}")

    r = await api_patch(c, f"/marketplace/cart/{item_id}", {"quantity": 3}, ctok)
    record("cart: update qty", r.status_code == 200, f"{r.status_code}")

    # ── order ──
    order = {"items": [{"product_id": pid, "name": "E2E Brake Pad", "quantity": 1, "price": "1199.00"}],
             "address": {"line1": "12 Test St", "city": "Coimbatore", "pincode": "641001"}}
    r = await api_post(c, "/orders", order, ctok)
    record("orders: create", r.status_code == 201, f"{r.status_code} {r.text[:150]}")

    r = await api_get(c, "/orders", ctok)
    record("orders: list", r.status_code == 200, f"{r.status_code}")

    # ── favorites + review ──
    r = await api_post(c, "/favorites", {"product_id": pid}, ctok)
    record("favorites: add", r.status_code in (200, 201), f"{r.status_code}")
    r = await api_get(c, "/favorites", ctok)
    record("favorites: list", r.status_code == 200, f"{r.status_code}")
    r = await api_delete(c, f"/favorites/{pid}", ctok)
    record("favorites: remove", r.status_code in (200, 204), f"{r.status_code}")

    r = await api_post(c, f"/marketplace/products/{pid}/reviews",
                       {"rating": 5, "text": "E2E", "userName": "E2E"}, ctok)
    record("reviews: product review", r.status_code == 201, f"{r.status_code} {r.text[:120]}")

    # ── seller order visibility + product cleanup ──
    r = await api_delete(c, f"/marketplace/cart/{item_id}", ctok)
    r = await api_delete(c, f"/marketplace/products/{pid}", stok)
    record("seller: delete product", r.status_code in (200, 204), f"{r.status_code}")
    CREATED_PRODUCT_IDS.discard(pid)


async def t_fleet(c: httpx.AsyncClient, ts: int):
    owner = await signup(c, "customer", ts + 100)
    ftok = owner["token"]

    body = {"companyName": f"E2E Logistics {ts}", "fleetType": "logistics", "fleetSize": "11-50",
            "contactName": "E2E Fleet Manager", "contactEmail": f"e2e_customer_{ts + 100}@{DOMAIN}",
            "contactPhone": f"97{ts % 100000000:08d}", "businessAddress": "1 Fleet Road, Coimbatore"}
    r = await api_post(c, "/fleet/register", body, ftok)
    record("fleet: register", r.status_code == 201, f"{r.status_code} {r.text[:150]}")

    r = await api_post(c, "/fleet/register", body, ftok)
    record("fleet: duplicate register rejected", r.status_code == 409, f"{r.status_code}")

    r = await api_get(c, "/fleet/my-fleet", ftok)
    record("fleet: my-fleet", r.status_code == 200, f"{r.status_code}")

    r = await api_post(c, "/fleet/bookings", {
        "scheduledAt": "2026-09-20T10:00:00Z",
        "vehicles": [{"vehicleName": "TN01AB1234", "serviceType": "periodic"}],
        "subtotal": 3000, "discountPercent": 10, "total": 2700}, ftok)
    record("fleet: create booking", r.status_code == 201, f"{r.status_code} {r.text[:150]}")

    r = await api_get(c, "/fleet/bookings", ftok)
    record("fleet: list bookings", r.status_code == 200, f"{r.status_code}")


async def t_job_chat_pay(c: httpx.AsyncClient, ts: int, base: str):
    """customer posts job → mechanic sees offer → accept → chat WS+history → finalize → cash → review."""
    mech = await signup(c, "mechanic", ts + 200)
    mtok = mech["token"]
    cust = await signup(c, "customer", ts + 201)
    ctok = cust["token"]

    r = await api_post(c, "/service/request",
                       {"issueTag": "engine", "description": f"E2E engine trouble {ts}", "requestType": "auto",
                        "customerLat": 11.0168, "customerLng": 76.9558}, ctok)
    record("job: create request", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    job_id = r.json().get("id")

    r = await api_get(c, "/providers/offers", params={"status": "pending"}, token=mtok)
    offers = r.json().get("offers", []) if r.status_code == 200 else []
    offer = next((o for o in offers if o.get("jobId") == job_id), None)
    record("job: mechanic sees offer (dispatch)", offer is not None, f"{r.status_code} offers={len(offers)}")

    if offer:
        r = await api_post(c, f"/offers/{offer['id']}/accept", {}, mtok)
        record("job: accept offer", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = await api_get(c, "/jobs/incoming", mtok)
    jobs = r.json().get("jobs", []) if r.status_code == 200 else []
    record("job: mechanic incoming", any(j.get("id") == job_id for j in jobs), f"{r.status_code} n={len(jobs)}")

    # ── chat: WS send (server persists), then verify via REST history ──
    ws_sent = ws_echo = False
    detail = ""
    try:
        import websockets
        ws_url = f"ws://{urlparse(base).netloc}/ws" if base.startswith("http://") else \
                 f"wss://{urlparse(base).netloc}/ws"
        async with websockets.connect(ws_url, subprotocols=[ctok], open_timeout=30) as ws:
            await ws.send(json.dumps({"type": "CHAT_MESSAGE", "payload": {"jobId": job_id, "text": "hello from e2e"}}))
            ws_sent = True
            try:
                got = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
                ws_echo = got.get("type") == "CHAT_MESSAGE"
            except asyncio.TimeoutError:
                pass
        detail = f"sent={ws_sent} echo={ws_echo}"
    except Exception as e:  # noqa: BLE001
        detail = f"{type(e).__name__}: {e}"
    record("chat: WS connected + sent", ws_sent, detail)

    r = await api_get(c, f"/chat/history/{job_id}", ctok)
    hist = r.json()
    msgs = hist.get("messages", hist) if isinstance(hist, dict) else hist
    record("chat: WS message persisted to history",
           r.status_code == 200 and any(m.get("text") == "hello from e2e" for m in msgs),
           f"{r.status_code} n={len(msgs)}")

    # ── lifecycle: walk assigned→en_route→in_progress, finalize → cash → review ──
    for st in ("en_route", "in_progress"):
        r = await api_patch(c, f"/service/request/{job_id}/status", {"status": st}, mtok)
        if r.status_code != 200:
            record(f"job: status → {st}", False, f"{r.status_code} {r.text[:120]}")
            break
    else:
        record("job: status walk en_route→in_progress", True)
        r = await api_post(c, f"/service/request/{job_id}/finalize-price", {"serviceAmount": 850}, mtok)
        record("job: finalize-price", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = await api_post(c, "/payments/cash", {"job_id": job_id, "amount": 85000}, mtok)
    record("payments: cash (mechanic collects)", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = await api_get(c, "/payments/history", ctok)
    record("payments: history", r.status_code == 200, f"{r.status_code}")

    r = await api_post(c, "/reviews", {"job_id": job_id, "rating": 5, "comment": "E2E great"}, ctok)
    record("reviews: job review", r.status_code in (200, 201), f"{r.status_code} {r.text[:120]}")


async def t_forgot_password(c: httpx.AsyncClient, ts: int):
    """Full reset flow: request → code read from Redis (server-local) → reset → login."""
    b = signup_body("customer", ts + 300)
    await signup(c, "customer", ts + 300)

    r = await api_post(c, "/auth/forgot-password/request", {"email": b["email"]})
    record("reset: request code", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    code = await redis_get(f"reset:{b['email']}")
    if not code:
        record("reset: code retrievable from redis", False, "no code")
        return

    r = await api_post(c, "/auth/forgot-password/reset",
                       {"email": b["email"], "code": "wrong12345", "newPassword": "NewE2e123!"})
    record("reset: wrong code rejected", r.status_code in (400, 401, 403, 429), f"{r.status_code}")

    r = await api_post(c, "/auth/forgot-password/reset",
                       {"email": b["email"], "code": code, "newPassword": "NewE2e123!"})
    record("reset: correct code accepted", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    r = await api_post(c, "/auth/login", {"email": b["email"], "password": "NewE2e123!"})
    record("reset: login with new password", r.status_code == 200, f"{r.status_code}")


async def t_kyc_admin(c: httpx.AsyncClient, ts: int):
    """KYC: mechanic signs up with docs → status submitted → admin approves → verified; non-admin rejected."""
    docs = {"aadhaarPhotoUrl": f"https://{DOMAIN}/aadhaar.png", "licensePhotoUrl": f"https://{DOMAIN}/license.png"}
    mech = await signup(c, "mechanic", ts + 400, **docs)
    ktok = mech["token"]

    r = await api_get(c, "/profile/me", ktok)
    st = r.json().get("kycStatus") if r.status_code == 200 else None
    record("kyc: status=submitted after docs", st == "submitted", f"got {st}")

    admin_tok = await bootstrap_admin(c, ts)
    if not admin_tok:
        record("kyc: admin bootstrap", False, "could not create admin")
        return

    r = await api_get(c, "/admin/kyc/pending", admin_tok)
    apps = r.json().get("applications", []) if r.status_code == 200 else []
    mine = next((a for a in apps if a.get("email") == f"e2e_mechanic_{ts + 400}@{DOMAIN}"), None)
    record("kyc: admin queue shows application", mine is not None, f"{r.status_code} n={len(apps)}")

    if mine:
        r = await api_patch(c, f"/admin/kyc/{mine['profileType']}/{mine['id']}/review",
                            {"action": "approve", "note": "e2e"}, admin_tok)
        record("kyc: admin approve", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

        r = await api_get(c, "/profile/me", ktok)
        st2 = r.json().get("kycStatus") if r.status_code == 200 else None
        record("kyc: status=verified after approve", st2 == "verified", f"got {st2}")

    cust = await signup(c, "customer", ts + 401)
    r = await api_get(c, "/admin/kyc/pending", cust["token"])
    record("kyc: non-admin rejected", r.status_code in (401, 403), f"{r.status_code}")


async def bootstrap_admin(c: httpx.AsyncClient, ts: int) -> str | None:
    """Promote a fresh suite user to admin directly in DB (suite runs on the server)."""
    b = signup_body("customer", ts + 500)
    d = await signup(c, "customer", ts + 500)
    uid = d.get("user", {}).get("id")
    if not uid or not await psql(f"UPDATE users SET role='admin', is_superuser=true WHERE id='{uid}';"):
        return None
    try:
        d2 = await login(c, b["email"], b["password"])
        return d2["token"]
    except AssertionError:
        return None


# ────────────────────────── server-local helpers (psql / redis) ──────────────────────────


async def _run(cmd: list[str], env: dict | None = None) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", **(env or {})},
        )
        out, _ = await proc.communicate()
        return out.decode().strip() if proc.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


async def redis_get(key: str) -> str | None:
    out = await _run(["redis-cli", "-h", "127.0.0.1", "GET", key])
    return out if out and out != "(nil)" else None


async def psql(sql: str) -> bool:
    out = await _run(["psql", "-h", "127.0.0.1", "-U", "clutchd", "-d", "clutchd", "-c", sql],
                     env={"PGPASSWORD": "clutchd"})
    return out is not None


async def cleanup():
    """Delete everything the suite created (FK-safe order). Never raises."""
    if not CREATED_USER_IDS and not CREATED_PRODUCT_IDS:
        return
    # UUIDs must be single-quoted in SQL — unquoted ids are a syntax error.
    ids = ",".join(f"'{u}'" for u in sorted(CREATED_USER_IDS))
    for pid in CREATED_PRODUCT_IDS:
        await psql(f"DELETE FROM marketplace_products WHERE id='{pid}';")
    if ids:
        mech_ids = f"SELECT id FROM mechanics WHERE user_id IN ({ids})"
        gar_ids = f"SELECT id FROM garages WHERE user_id IN ({ids})"
        job_ids = f"SELECT id FROM jobs WHERE user_id IN ({ids})"
        for sql in (
            f"DELETE FROM marketplace_cart_items WHERE user_id IN ({ids});",
            f"DELETE FROM marketplace_order_items WHERE order_id IN (SELECT id FROM marketplace_orders WHERE user_id IN ({ids}));",
            f"DELETE FROM marketplace_orders WHERE user_id IN ({ids});",
            f"DELETE FROM payments WHERE user_id IN ({ids}) OR job_id IN ({job_ids});",
            # reviews: reviewer FK cascades, but target_mechanic/garage are NO ACTION
            f"DELETE FROM reviews WHERE reviewer_user_id IN ({ids}) OR target_mechanic_id IN ({mech_ids}) OR target_garage_id IN ({gar_ids});",
            f"DELETE FROM referral_rewards WHERE referred_user_id IN ({ids});",
            f"DELETE FROM fleet_bookings WHERE user_id IN ({ids});",
            f"DELETE FROM fleets WHERE user_id IN ({ids});",
            f"DELETE FROM audit_logs WHERE user_id IN ({ids});",
            f"DELETE FROM users WHERE id IN ({ids});",
        ):
            ok = await psql(sql)
            if not ok:
                print(f"[cleanup] WARNING failed: {sql[:90]}...", flush=True)
    print(f"\n[cleanup] removed {len(CREATED_USER_IDS)} suite users, {len(CREATED_PRODUCT_IDS)} products", flush=True)


# ────────────────────────── main ──────────────────────────


async def main() -> int:
    local = "--local" in sys.argv
    base = LOCAL_BASE if local else PUBLIC_BASE
    ts = int(time.time())

    print(f"=== ClutchD E2E suite -> {base} ===\n", flush=True)
    try:
        async with make_client(base) as c:
            await t_health(c)
            creds = await t_auth(c, ts)
            await t_profile(c, creds["token"])
            await t_marketplace_catalog(c)
            await t_seller_and_cart(c, ts)
            await t_fleet(c, ts)
            await t_job_chat_pay(c, ts, base)
            await t_forgot_password(c, ts)
            await t_kyc_admin(c, ts)
    finally:
        await cleanup()

    print(f"\n=== RESULTS: {len(PASS)} passed, {len(FAIL)} failed ===")
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
