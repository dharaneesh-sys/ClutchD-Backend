---
slug: phase-2c-push-notifications
status: awaiting-approval
intent: clear
review_required: false
pending-action: write .omo/plans/phase-2c-push-notifications.md
approach: FCM push via firebase-admin SDK, Celery task for async delivery, DeviceToken model+endpoints for token lifecycle, wired into offer creation
---

# Draft: phase-2c-push-notifications

## Components (topology ledger)

| id | outcome | status | evidence path |
|----|---------|--------|---------------|
| in-app-notification-model | Notification ORM exists, DB-persisted, user-scoped | active | models/notification.py:10-20 |
| notification-preferences | UserSettings with push/sms/email booleans exists | active | models/new_models.py:146-161 |
| notification-api | GET/PATCH notifications, WS broadcast on read | active | api/v1/notifications.py |
| websocket-infra | ConnectionManager, /ws + /ws/tracking/{job_id}, auth | active | ws/manager.py, main.py:175-319 |
| celery-worker | send_notification stub (no-op) exists | active | tasks/worker.py:50-52 |
| **device-token-model** | **MISSING** — no DeviceToken ORM, no token storage | **active** | explored all models/ files |
| **fcm-integration** | **MISSING** — no firebase-admin dep, service, or config | **active** | requirements.txt, core/config.py |
| **device-token-api** | **MISSING** — no register/refresh/delete endpoints | **active** | api/v1/ (all routes scanned) |
| **push-on-offer** | **MISSING** — offer_service.py does NOT push | **active** | services/offer_service.py |
| **test-infra** | **MISSING** for push — no tests exist | **active** | tests/ |

## Findings (cited — path:lines)

1. **Notification model**: `models/notification.py` — id(UUID PK), user_id(FK→users), title(128), body(Text), type("system"/"job_update"/"payment"), read(bool), job_id(FK→jobs), created_at. In-app only, no push fields.

2. **UserSettings preferences**: `models/new_models.py:146-161` — push_notifications: bool = True (unused — no sender exists)

3. **Celery send_notification stub**: `tasks/worker.py:50-52` — `@celery_app.task(name="notify.generic")` def send_notification(payload): logger.info("Notification task: %s", payload). No-op.

4. **WebSocket manager**: `ws/manager.py` — `ConnectionManager` with `_user_sockets: dict[str, set[WebSocket]]`, `connect_user/disconnect_user/send_json_to_user`, `push_status_update/push_location_update` helpers.

5. **offer_service.py**: `services/offer_service.py` — `create_provider_offers()` inserts ProviderOffer records but does NOT send any push notification.

6. **job_service notification pattern**: `services/job_service.py:293-323` — creates `Notification` DB record + broadcasts `NOTIFICATION_UPDATE` via WebSocket on status changes. This is the established pattern for in-app notification.

7. **Vehicle model**: `models/vehicle.py` — has `make`, `model`, `year` fields linked via FK to user_id. But job→vehicle via `vehicle_id` on Job model. Vehicle info available for notification body if desired.

8. **Migration HEAD**: `d1e2f3a4b5c6` (provider_offers table) with parent `c1d2e3f4a5b6`.

9. **FCM patterns (external research)**:
   - `firebase_admin.credentials.Certificate()` accepts dict — can init from env var JSON string
   - Must `.replace("\\n", "\n")` on private_key to fix newline mangling
   - `messaging.Message(notification=Notification(title, body), data={...}, token=...)` + `messaging.send(message)`
   - `send_each_for_multicast(MulticastMessage(..., tokens=[...]))` for batch (send_all/multicast deprecated in v7)
   - Catch `messaging.UnregisteredError` / `SenderIdMismatchError` → remove token from DB
   - firebase-admin is synchronous — must run in Celery or ThreadPoolExecutor

## Decisions (with rationale)

1. **Push provider: Firebase Cloud Messaging (FCM)**
   - Chosen over raw APNs (cross-platform), OneSignal (third-party dependency), web push (only for web)
   - Standard for Android + iOS via Capacitor app (project uses Capacitor — see cors_origins in config)
   - firebase-admin SDK is well-maintained, mature, 532+ code snippets available

2. **Device token model: FK to users, not providers**
   - Mechanics/garages are Users with a role. A user can be both mechanic and garage.
   - Device tokens belong to the user, not the provider profile. Simplifies relationship.
   - Additional fields: platform (android/ios), is_active (soft-delete for invalid tokens)

3. **Push delivery via Celery task (async)**
   - firebase-admin SDK is synchronous (blocking HTTP calls). Celery keeps API response fast.
   - Existing `send_notification` Celery stub can be extended rather than replaced.
   - ThreadPoolExecutor alternative would block an API worker — Celery is the right pattern.

4. **Notification content: offer_id + job summary only (no sensitive data)**
   - FCM data payload: `{type: "new_offer", offerId: "...", jobId: "..."}`
   - FCM notification (display): `title: "New Service Request"`, `body: "<issue_tag> • <distance> away"`
   - No customer name, address, phone, or vehicle VIN in push payload
   - Provider fetches full details from `GET /providers/offers` (existing endpoint from Phase 2B)

5. **Preference check: respect UserSettings.push_notifications**
   - Before sending push, check the user's notification preference
   - If push_notifications = False, skip push but still create in-app Notification record

6. **firebase-admin version: track stable (no v7+ migration risk)**
   - Use firebase-admin ~= 6.9 or latest stable; avoid v7.0.0+ send_multicast deprecation footgun
   - Specifically use `send_each_for_multicast` (works in both v6 & v7) rather than deprecated `send_multicast`

## Scope IN

1. **DeviceToken ORM model** — `models/device_token.py`:
   - id (UUID PK), user_id (FK→users), token (Text, unique), platform (String: "android"/"ios"), is_active (bool), created_at, updated_at
   - UniqueConstraint on user_id + token
   - Index on token for fast lookup

2. **DeviceToken API endpoints** — `api/v1/device_tokens.py`:
   - `POST /providers/device-tokens` — register/refresh device token (body: `{token, platform}`), authenticated, returns token record
   - `DELETE /providers/device-tokens/{id}` — remove/unregister device token, owner-only
   - Both rate-limited 10/min

3. **FCM initialization** — `core/firebase.py`:
   - Singleton init: read `FIREBASE_SERVICE_ACCOUNT` env var → `json.loads` → `credentials.Certificate()` → `initialize_app()`
   - Graceful skip if env var absent (no crash, just log warning)

4. **Push notification service** — `services/push_service.py`:
   - `send_offer_push(db, job, provider_type, provider_id, distance)` — builds FCM Message, sends, handles UnregisteredError
   - `send_offer_push_batch(db, job, provider_type, provider_ids_with_distance)` — batch via send_each_for_multicast
   - Handles: UnregisteredError/SenderIdMismatchError → deactivate token; other FirebaseError → log + retry

5. **Wire push into offer creation**:
   - After `create_provider_offers()` creates ProviderOffer records, dispatch Celery task `notify.new_offer`
   - Task queries device tokens for each provider, checks push_notifications preference, sends FCM
   - Also create Notification DB record for in-app delivery

6. **Celery task: extend `send_notification` → new `notify.new_offer`**:
   - Receives job_id + list of (provider_type, provider_id) pairs
   - Queries DeviceToken for those providers' user_ids
   - Checks UserSettings.push_notifications
   - Sends via FCM, deactivates invalid tokens
   - Creates Notification DB record as fallback

7. **Settings** — `core/config.py`:
   - Add `fcm_service_account: str | None = None` (reads from `FIREBASE_SERVICE_ACCOUNT` env var)
   - Add `fcm_enabled: bool` property (True if fcm_service_account is set)

8. **Env files** — `.env.example`, `env.docker.example`, `docker-compose.yml`:
   - Add `FIREBASE_SERVICE_ACCOUNT` (commented out with instructions)

9. **Migration** — `d2e3f4a5b6c7_add_device_tokens_table.py`:
   - Creates `device_tokens` table with all columns
   - down_revision: `d1e2f3a4b5c6`

10. **Tests**:
    - Unit: DeviceToken model validation
    - Unit: push_service error handling (mock FCM)
    - API: POST/DELETE /providers/device-tokens (auth, role guard, validation)
    - Integration: offer creation + push notification dispatch

## Scope OUT (Must NOT have)

1. ❌ **No APNs direct integration** — FCM covers Android + iOS via Firebase
2. ❌ **No Web Push / VAPID** — not applicable for native mobile app
3. ❌ **No SMS or email sending** — preferences exist, but out of scope
4. ❌ **No WebSocket push for providers** — existing WS is customer-facing (tracking/location)
5. ❌ **No real device delivery testing** — unless FCM credentials are provided and configured
6. ❌ **No client-side code** — device token registration is server-side API only
7. ❌ **No offer acceptance/rejection logic** — Phase 2C ends at push notification delivery
8. ❌ **No UI/UX for notification preferences** — existing UserSettings API handles this
9. ❌ **No rate limiting push sending** — FCM has its own quota; let user's plan handle it
10. ❌ **Do NOT modify existing provider_offer creation logic** — push is additive, not a replacement

## Open questions

- (none — all forks resolved by research)

## Approval gate

status: awaiting-approval

Brief presented below. When approved, I will create the full `.omo/plans/phase-2c-push-notifications.md` with detailed implementation todos, acceptance criteria, commit guidance, and the final verification wave.
