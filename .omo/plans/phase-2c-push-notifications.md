---
slug: phase-2c-push-notifications
status: approved
intent: clear
review_required: false
approach: FCM push via firebase-admin SDK, Celery task for async delivery, DeviceToken model+endpoints for token lifecycle, wired into offer creation
---

# Phase 2C — Real Mechanic Push Notifications

## Pre-Requisites
- Migration HEAD: `d1e2f3a4b5c6` (from Phase 2B — provider_offers table)
- All Phase 2B code in place (ProviderOffer model, offer_service, matching filters)
- Firebase project required for actual FCM delivery (plan implements up to config boundary)

## Todos

- [x] 1. Create DeviceToken ORM model
- [x] 2. Generate Alembic migration for device_tokens table
- [x] 3. Add FCM config to Settings
- [x] 4. Add FCM init module (core/firebase.py)
- [x] 5. Add firebase-admin to requirements
- [x] 6. Create push notification service (services/push_service.py)
- [x] 7. Create device token API endpoints (POST/DELETE /providers/device-tokens)
- [x] 8. Wire push dispatch into offer creation + Celery notify.new_offer task
- [x] 9. Update env files (.env.example, env.docker.example, docker-compose.yml)
- [x] 10. Write tests (device token model, push service, API, integration)

## Implementation details

### 1. Add DeviceToken model
**File:** `backend/app/models/device_token.py`

- Fields:
  - `id: Mapped[uuid.UUID]` = UUID PK, default=uuid.uuid4
  - `user_id: Mapped[uuid.UUID]` = FK("users.id", ondelete="CASCADE"), index, nullable=False
  - `token: Mapped[str]` = Text, unique, nullable=False (the FCM device registration token)
  - `platform: Mapped[str]` = String(16), nullable=False ("android" / "ios")
  - `is_active: Mapped[bool]` = Boolean, default=True (soft-delete for invalid tokens)
  - `created_at: Mapped[datetime]` = DateTime(timezone=True), server_default=func.now()
  - `updated_at: Mapped[datetime]` = DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
- __table_args__: UniqueConstraint("user_id", "token", name="uq_user_device_token")
- Relationship: `user: Mapped["User"] = relationship("User", back_populates="device_tokens")`
- Register in `models/__init__.py` and `models/__all__`
- Add `device_tokens: Mapped[list["DeviceToken"]]` relationship to `models/user.py`

**Acceptance:** Importable from `app.models.device_token`, accessible as `User.device_tokens`, `models.__all__` includes `"DeviceToken"`.

**QA:** `python -c "from app.models.device_token import DeviceToken; print('OK')"` succeeds.

**Commit:** `feat: add DeviceToken ORM model for FCM device registration`

---

### 2. Generate Alembic migration for device_tokens table
**File:** `backend/migrations/versions/d2e3f4a5b6c7_add_device_tokens_table.py`

- `down_revision = "d1e2f3a4b5c6"` (current HEAD from Phase 2B)
- `op.create_table(
    "device_tokens",
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, ...),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("token", sa.Text(), nullable=False),
    sa.Column("platform", sa.String(16), nullable=False),
    sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.UniqueConstraint("user_id", "token", name="uq_user_device_token"),
    sa.UniqueConstraint("token", name="uq_device_token"),
)`
- Downgrade: `op.drop_table("device_tokens")`
- Use `sa.text("gen_random_uuid()")` for server_default of UUID if applicable, or keep Python-side default

**Acceptance:** `scripts/makemigration.sh` not needed — migration is hand-crafted. `scripts/migrate.sh` applies it without error.

**QA:** Check migration HEAD: `python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; ..."` confirms `d2e3f4a5b6c7` is current.

**Commit:** `feat: add device_tokens migration`

---

### 3. Add FCM config to Settings
**File:** `backend/app/core/config.py`

- Add field: `fcm_service_account: str | None = None` (reads from `FIREBASE_SERVICE_ACCOUNT` env var)
- Add @property: `fcm_enabled` returning `True` if `self.fcm_service_account` is set
- Place alongside existing provider configs (stripe, razorpay, google_oauth) for consistency

**Acceptance:** `get_settings().fcm_enabled` returns `False` when env var unset, `True` when set.

**Commit:** `feat: add FCM service account config to Settings`

---

### 4. Add FCM init module
**File:** `backend/app/core/firebase.py`

- Singleton `_firebase_app: firebase_admin.App | None = None`
- `init_firebase()` function:
  - Guard: skip if already initialized (`firebase_admin._apps`)
  - Read `get_settings().fcm_service_account` → if None, log warning and return
  - `json.loads()` the JSON string
  - CRITICAL: `.replace("\\n", "\n")` on `private_key` (newline mangling fix)
  - `cred = credentials.Certificate(cred_dict)`
  - `firebase_admin.initialize_app(cred)`
  - Log success
- `get_firebase_app()` → returns app or None (for callers to check)
- Import in `main.py` and call `init_firebase()` during lifespan (before `yield`)
- Log warning at startup if FCM not configured

**Acceptance:** Server starts without crash if `FIREBASE_SERVICE_ACCOUNT` is unset; prints "[WARNING] FCM not configured — push notifications disabled". Server initializes FCM if env var is set.

**QA:** `python -c "from app.core.firebase import init_firebase, get_firebase_app; init_firebase(); print(get_firebase_app())"` succeeds (app is None when unconfigured).

**Commit:** `feat: add Firebase Admin SDK initialization module`

---

### 5. Add firebase-admin dependency
**File:** `backend/requirements.txt`

- Add line: `firebase-admin>=6.9.0,<8.0.0` (range avoids v7 breaking changes while allowing latest stable)
- Keep alphabetical ordering within the auth/security section (add after `bcrypt`, before `setuptools`)

**Acceptance:** `pip install -r requirements.txt` installs firebase-admin without conflict.

**Commit:** `chore: add firebase-admin dependency`

---

### 6. Create push notification service
**File:** `backend/app/services/push_service.py`

Functions:

```python
async def send_new_offer_push(
    db: AsyncSession,
    *,
    device_token: DeviceToken,
    job: Job,
    offer: ProviderOffer,
    distance_m: float,
) -> bool:
    """Send FCM push for a new offer to one device. Returns True if sent successfully."""
```

- Build notification payload:
  - `title = "New Service Request"`
  - `body = f"{job.issue_tag} • {distance_km:.1f} km away"`
  - `data = {"type": "new_offer", "offerId": str(offer.id), "jobId": str(job.id)}`
- Check `device_token.is_active` — skip if inactive
- Check `UserSettings.push_notifications` for the token's user — skip if disabled
- `message = messaging.Message(notification=Notification(title, body), data=data, token=device_token.token)`
- `messaging.send(message)` wrapped in try/except:
  - `UnregisteredError` / `SenderIdMismatchError` → set `device_token.is_active = False`, log
  - `UnavailableError` → log warning (FCM transient, no retry for push)
  - `FirebaseError` → log error (unknown failure, don't deactivate token)
- Return bool (success)

```python
async def send_new_offer_batch(
    db: AsyncSession,
    *,
    job: Job,
    token_offer_pairs: list[tuple[DeviceToken, ProviderOffer, float]],
) -> tuple[int, int]:
    """Send FCM pushes for a new offer to multiple devices. Returns (success_count, failure_count)."""
```

- Loop pairs, filter by is_active + push_notifications preference
- Use `send_each_for_multicast` with batch of up to 500 tokens (identical message)
- Handle per-response UnregisteredError → deactivate specific token
- Return (success, failure) counts

Edge cases handled:
- No device tokens for provider → skip gracefully
- User has push_notifications=False → skip push, relies on in-app notification
- All tokens inactive → skip
- FCM temporarily unavailable → logged, not retried (offers are time-sensitive but user can refresh)

**Acceptance:** All functions importable, mockable, handle all FCM error types gracefully.

**QA:** Unit test with mocked `messaging.send()` that raises `UnregisteredError` — verify `is_active` set to False.

**Commit:** `feat: create push notification service with FCM integration`

---

### 7. Create device token API endpoints
**File:** `backend/app/api/v1/device_tokens.py`

```python
router = APIRouter(prefix="/providers/device-tokens", tags=["providers"])
```

**POST /providers/device-tokens** — Register/refresh device token
- Request body: `{token: str, platform: str}` (platform: "android" | "ios")
- Auth: `CurrentUser` (any role — customers can get push too)
- Rate limit: 10/minute
- Logic:
  - Check existing token by `token` value → if exists and belongs to this user, reactivate and update platform
  - If exists and belongs to different user → 409 Conflict (unlikely but safe)
  - If new → create DeviceToken record
  - Return 201 with device token record (excluding the raw token? Keep it — client needs to confirm)
- Response: `DeviceTokenResponse` schema

**DELETE /providers/device-tokens/{token_id}** — Unregister device
- Path param: token_id (UUID)
- Auth: `CurrentUser` + owner check (token.user_id must match)
- Soft-delete: set `is_active = False` (don't delete the row — audit trail)
- Return 204 No Content

**GET /providers/device-tokens** — List own device tokens (optional, for debugging)
- Return list of user's active device tokens (count + basic info)
- Limit 10, ordered by created_at desc

Schemas in `backend/app/schemas/device_token.py`:
- `DeviceTokenRegisterBody(BaseModel): token: str; platform: Literal["android", "ios"]`
- `DeviceTokenResponse(BaseModel): id: UUID; platform: str; is_active: bool; created_at: datetime`

Wire into `router.py`: `api_router.include_router(device_tokens.router)`

**Acceptance:**
- 401 without auth
- 201 with valid body
- 409 if token already registered to another user
- 204 on delete
- 404 if token_id not found or not owned by user
- Rate limited at 10/min

**Commit:** `feat: add device token registration and management endpoints`

---

### 8. Wire push into offer creation
**File:** `backend/app/services/offer_service.py`

After `for m in mechs:` and `for g in gars:` loops (after all offers inserted):

```python
# Dispatch push notification Celery task
if total > 0:
    from app.tasks.worker import dispatch_offer_push
    dispatch_offer_push.apply_async(args=[str(locked_job.id), total], countdown=2)
```

This fires the Celery task 2 seconds after offer creation (small delay to ensure DB commit).

**File:** `backend/app/tasks/worker.py`

Extend with new task:

```python
@celery_app.task(name="notify.new_offer", max_retries=1, default_retry_delay=30)
def dispatch_offer_push(job_id: str, expected_count: int) -> None:
    """Send FCM push notifications to providers who received offers for this job.
    
    Queries ProviderOffer records for the job, finds the users behind each provider,
    gathers their active device tokens, respects push_notifications preference,
    sends FCM push, deactivates invalid tokens, and creates in-app Notification records.
    """
```

Implementation:
1. Open async session
2. Load job by UUID
3. Query all ProviderOffer for this job with `status="pending"`
4. For each offer:
   a. Look up the provider's user_id (via Mechanic user or Garage user)
   b. Query DeviceToken where user_id IN (provider user_ids) AND is_active=True
   c. Query UserSettings, check push_notifications
   d. Also create an in-app Notification record (title, body, type="job_update", job_id=job.id)
5. Send batch FCM push via push_service.send_new_offer_batch
6. Log counts: "Dispatched X push notifications for job Y (Z providers, W devices)"
7. On UnregisteredError from service — tokens already deactivated in service layer

Also create in-app Notification records alongside the push (existing pattern from job_service.py).

**Acceptance:** When `create_provider_offers` returns > 0, the Celery task is dispatched. The task queries offers, finds device tokens, respects preferences, sends FCM, and creates in-app Notification records.

**QA:** Mock Celery in integration test — verify `dispatch_offer_push.apply_async` is called with correct args.

**Commit:** `feat: wire push notification dispatch into offer creation flow; add Celery task notify.new_offer`

---

### 9. Update env files and docker-compose
**Files:** `.env.example`, `backend/env.docker.example`, `docker-compose.yml`

**.env.example** — Add after existing Google OAuth block:
```env
# Firebase Cloud Messaging (optional — leave empty to disable push notifications)
# Create a service account at https://console.firebase.google.com/ > Project settings > Service accounts
# Copy the entire JSON and paste it as a single line (or use a config injector)
FIREBASE_SERVICE_ACCOUNT=
```

**env.docker.example** — Same addition.

**docker-compose.yml** — Add to `api` service environment section:
```yaml
FIREBASE_SERVICE_ACCOUNT: ${FIREBASE_SERVICE_ACCOUNT:-}
```
Add to `worker` service environment section (same — worker needs FCM access too):
```yaml
FIREBASE_SERVICE_ACCOUNT: ${FIREBASE_SERVICE_ACCOUNT:-}
```

**Acceptance:** Docker compose file validates; env files document the variable.

**Commit:** `chore: add FIREBASE_SERVICE_ACCOUNT to env configuration`

---

### 10. Write tests
**Test files:**

**tests/test_device_token_model.py** (4 tests)
1. Create DeviceToken — verify fields set, token stored, UUID generated
2. Unique constraint on token — insert duplicate, expect IntegrityError
3. Unique constraint on (user_id, token) — same user+token, expect IntegrityError
4. Relationship — `user.device_tokens` returns tokens

**tests/test_push_service.py** (6 tests — mock messaging.send)
1. `send_new_offer_push` — success case, returns True
2. `send_new_offer_push` — UnregisteredError → token deactivated, returns False
3. `send_new_offer_push` — SenderIdMismatchError → token deactivated
4. `send_new_offer_push` — push_notifications=False → skipped
5. `send_new_offer_push` — inactive token → skipped
6. `send_new_offer_batch` — mixed success/failure (mock send_each_for_multicast returning BatchResponse with both)

**tests/test_api_device_tokens.py** (6 tests)
1. POST without auth → 401
2. POST with valid body → 201, token stored
3. POST duplicate token → 409
4. POST duplicate token same user → 200 (refresh/reactivate)
5. DELETE own token → 204, is_active=False
6. DELETE other's token → 404
7. GET list → 200, returns tokens

**tests/test_offer_push_integration.py** (3 tests)
1. `create_provider_offers` dispatches Celery task (mock `dispatch_offer_push.apply_async`)
2. `dispatch_offer_push` creates in-app Notification records + sends FCM
3. Provider with no device tokens — push skipped, Notification still created? Or skip everything? (Decision: if no tokens, skip push but still create in-app Notification)

**Supporting conftest additions:**
- Add `test_device_token` factory fixture (takes user + token override)
- Patch FCM globally in test config? No — mock per test via `unittest.mock.patch`

**Verification:** `cd backend && python -m pytest tests/ -v --tb=short` — all 19+ tests pass.

**Commit:** `test: add tests for device token model, push service, API, and offer-push integration`

---

## Final verification wave

- [x] F1. Read-through all new files — verify each file for correctness, import resolution, error handling
- [x] F2. FCM init graceful skip — run server without FIREBASE_SERVICE_ACCOUNT, expect no crash, only warning log
- [x] F3. All tests pass — `python -m pytest tests/ -v --tb=short`: ≥19 passed, 0 failed
- [x] F4. Plan scope compliance — verify no APNs direct, no web push, no SMS/email, no client code, no offer acceptance, no UI changes, no real-device delivery claim

## Dependencies between todos

```
1 (model) ──→ 2 (migration) ──→ 3 (config) ──→ 4 (firebase.py) ──→ 6 (push_service)
                                                     │
1 ──→ 7 (device_token API)                          │
                                                     ▼
1,6,7 ──→ 8 (wire into offer + Celery task) ──→ 10 (tests)
```

Todos 1, 3, 5 can run in parallel. To dos 2 depends on 1. Todo 9 (env files) can run anytime.

## External configuration required

To enable ACTUAL push notification delivery (beyond the service boundary):
1. A Firebase project (console.firebase.google.com)
2. A Firebase service account JSON key (Project settings > Service accounts > Generate new private key)
3. Set `FIREBASE_SERVICE_ACCOUNT` environment variable to the full JSON string (single-line escaped, or volume-mounted file path — implementer chooses env var approach)
4. Client-side: Firebase Admin SDK does NOT need client credentials — the service account is server-only
5. Mobile app must have Firebase SDK integrated and request FCM token on app start, then call `POST /providers/device-tokens` to register it

**Real-device delivery was NOT tested** — FCM credentials not available in this environment.
