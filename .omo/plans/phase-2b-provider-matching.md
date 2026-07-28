# phase-2b-provider-matching - Work Plan

## TL;DR (For humans)

**What you'll get:** When a customer requests roadside help, eligible nearby mechanics and garages get offers instead of the system picking one for them. Offers expire in 15 minutes. Providers can see their pending offers in a new endpoint.

**Why this approach:** The old system auto-assigned a job to the highest-ranked provider — unfair because nobody else had a chance. Phase 2B creates a fair matching layer: all eligible providers get an offer. Phase 2C (future) will let them accept. This is just the preparation step.

**What it will NOT do:** Providers still cannot accept offers (that's Phase 2C). No changes to the mobile app, no chat, no payments, no live tracking. The old auto-assignment function stays available for admin use.

**Effort:** Medium — 7 tasks across model, config, matching logic, new service, API endpoint, Celery integration, and 28+ tests.
**Risk:** Medium — a code audit found 4 issues that would have crashed on Render's managed PostGIS (no fallback path for radius/user-active filters). All are fixed in this plan.

**Decisions to sanity-check:** 25km search radius; 15-minute offer expiry; duplicate-safe inserts; new service file created from scratch; test infrastructure built from zero.

Your next move: approve, or run a high-accuracy review first.

---
> TL;DR (machine): Medium effort, Medium risk — 7 impl todos + 4 final verifiers. ProviderOffer model, matching.py eligibility update, offer creation service, Celery retry fix, GET /providers/offers, full test suite (28+ scenarios).

## Scope
### Must have
1. ProviderOffer model with UNIQUE(job_id, provider_type, provider_id) constraint + expires_at
2. Alembic migration (down_revision: c1d2e3f4a5b6) for provider_offers table
3. Search radius config: search_radius_km (default 25) + search_radius_m @property in Settings
4. Update ALL 8 query variants in matching.py:
   - Add penalized=false to mechanics and garages WHERE clauses
   - JOIN users table for u.is_active=true (BOTH PostGIS AND fallback paths)
   - Add ST_DWithin search radius filter (PostGIS path)
   - Add haversine-based radius filter in fallback path (skip rows beyond radius)
   - Keep existing _score(), haversine_m(), and sorting
5. New `offer_service.py` with create_provider_offers() that:
   - Acquires FOR UPDATE lock on job
   - Finds eligible providers via updated matching.py
   - Inserts ProviderOffer rows with ON CONFLICT DO NOTHING
   - Sets expires_at = now() + 15 min
6. Update create_service_request in job_service.py to call create_provider_offers instead of assign_job_auto
7. Update Celery retry_job_assignment in worker.py to call create_provider_offers
8. GET /providers/offers endpoint with:
   - Rate limit 30/min
   - Auth: mechanic or garage only, filtered to their own offers
   - Pagination: limit/offset
   - Filter: status=pending by default (can include expired with query param)
   - Sort: created_at DESC
   - WHERE (expires_at IS NULL OR expires_at > NOW()) default filter
9. Test infrastructure: pytest, pytest-asyncio, httpx, conftest.py with async fixtures
10. 28+ test scenarios covering eligibility, dedup, fallback, expiry, API, and integration
11. Register ProviderOffer in app/models/__init__.py
12. Update .env.example and docker-compose.yml with SEARCH_RADIUS_KM=25

### Must NOT have (guardrails, anti-slop, scope boundaries)
1. Provider acceptance flow (Phase 2C) — no PATCH/POST to accept/reject offers
2. Live tracking, chat, quotes, payments, WebSocket notifications
3. Any frontend changes (ClutchD-App is untouched)
4. Removing assign_job_auto() — preserve as utility function (no current callers after update)
5. Refactoring matching.py's raw SQL pattern — keep PostGIS + haversine fallback as-is
6. Changing any existing API response shapes (backward compatible only)
7. Celery Beat periodic cleanup task for expired offers (deferred to Phase 2C)
8. No new env vars beyond SEARCH_RADIUS_KM

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after (test infra must exist first) + pytest-asyncio + httpx
- Evidence: .omo/evidence/task-<N>-phase-2b-provider-matching.txt
- Test DB: SQLite (for unit/matching tests) or test PostgreSQL via env override

## Execution strategy
### Parallel execution waves
Wave 1 (infrastructure): ProviderOffer model + migration + config + test infra setup
Wave 2 (core logic): matching.py eligibility update + offer_service.py
Wave 3 (integration): job_service update + Celery retry + GET endpoint
Wave 4 (verification): all tests pass + final verification wave

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. ProviderOffer model | nothing | 2, 5 | 3 |
| 2. Migration | 1 | 5, 7 | 3 |
| 3. Search radius config | nothing | 4 | 1, 2 |
| 4. matching.py update | 3 | 5 | nothing |
| 5. offer_service.py | 1, 2, 4 | 6, 7 | nothing |
| 6. job_service.py + worker.py | 5 | 8 | nothing |
| 7. GET /providers/offers | 1, 2 | 8 | 6 |
| 8. Test infra + all tests | 6, 7 | verification wave | nothing |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [ ] 1. Create ProviderOffer model + search radius config + test infra skeleton
  What to do / Must NOT do:
  - Create `backend/app/models/provider_offer.py` with ProviderOffer ORM model:
    - id: UUID primary key
    - job_id: FK to jobs.id, NOT NULL
    - provider_type: String(32), NOT NULL (one of "mechanic", "garage")
    - provider_id: UUID, NOT NULL (FK to mechanics.id or garages.id depending on type)
    - status: String(32), default="pending"
    - expires_at: DateTime(timezone=True), NOT NULL
    - created_at: DateTime(timezone=True), server_default=func.now()
    - __table_args__: UniqueConstraint("job_id", "provider_type", "provider_id", name="uq_provider_offer")
    - Relationship to Job (offers: Mapped[list["ProviderOffer"]] = relationship(back_populates="offers"))
    - Must NOT use an existing model name clash (ClutchDOffer exists in new_models.py — different purpose)
  - Register in `backend/app/models/__init__.py`: import + add to __all__
  - Add relationship to Job model: `offers: Mapped[list["ProviderOffer"]] = relationship("ProviderOffer", back_populates="job")`
  - Add to `backend/app/core/config.py`:
    - `search_radius_km: float = 25.0` field
    - `search_radius_m: float` as @property returning `self.search_radius_km * 1000.0`
    - Must NOT be a stored field — must be @property
  - Update `backend/app/.env.example` and `backend/../docker-compose.yml` with `SEARCH_RADIUS_KM=25`
  - Create test infra skeleton:
    - `backend/tests/__init__.py` (empty)
    - `backend/tests/conftest.py` with async fixtures: test session, test client (httpx.AsyncClient), test user/mechanic/garage/job factories
    - Add pytest, pytest-asyncio, httpx to `backend/requirements.txt`
    - pytest config in `backend/pyproject.toml` or `backend/setup.cfg`
    - Must NOT depend on real PostgreSQL — use env-based URL override
  Parallelization: Wave 1 | Blocked by: nothing | Blocks: 2, 5
  References (executor has NO interview context - be exhaustive):
  - Existing model pattern: `backend/app/models/mechanic.py` (full model with UUID PK, FK, relationships)
  - Model registration: `backend/app/models/__init__.py` (import + __all__)
  - Job model for relationship: `backend/app/models/job.py:14-66` (add `offers` relationship)
  - Config pattern: `backend/app/core/config.py:8-85` (Settings class, add search_radius_km)
  - ClutchDOffer to avoid name clash: `backend/app/models/new_models.py:131-143` (different model — loyalty offers)
  - .env.example: `backend/.env.example`
  - docker-compose.yml: `backend/../docker-compose.yml` (add SEARCH_RADIUS_KM to api + worker env)
  - Migration HEAD: `backend/migrations/versions/c1d2e3f4a5b6_add_scheduled_at_to_jobs.py:17` (down_revision = "c1d2e3f4a5b6")
  - existing alembic env: `backend/migrations/env.py` (imports app.models, auto-detects new models)
  Acceptance criteria (agent-executable):
  - `cd backend && python -c "from app.models.provider_offer import ProviderOffer; print('OK')"` returns OK
  - `cd backend && python -c "from app.core.config import get_settings; s=get_settings(); assert s.search_radius_km==25; assert s.search_radius_m==25000; print('OK')"` returns OK
  - `cd backend && python -c "from app.models import ProviderOffer; print('OK')"` returns OK (registered in __init__)
  - `test -f backend/tests/conftest.py` returns 0
  - `test -f backend/tests/__init__.py` returns 0
  - `grep -q "pytest" backend/requirements.txt` returns 0
  - `grep -q "pytest-asyncio" backend/requirements.txt` returns 0
  - `grep -q "httpx" backend/requirements.txt` returns 0
  QA scenarios (name the exact tool + invocation): happy + failure, Evidence .omo/evidence/task-1-phase-2b-provider-matching.txt
  - HAPPY: `cd backend && python -c "from app.models import Job, ProviderOffer; j=Job(); assert hasattr(j, 'offers'); print('OK')"` — verify Job.offers relationship exists
  - HAPPY: `cd backend && grep -c "search_radius_km" backend/app/core/config.py` — verify config field exists
  - FAILURE: verify migration generation works: `cd backend && python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; from pathlib import Path; cfg=Config('alembic.ini'); script=ScriptDirectory.from_config(cfg); heads=script.get_heads(); print('heads:', heads); assert len(heads) > 0"`
  - FAILURE: verify migration HEAD is correct: check `backend/alembic.ini` or migration chain
  Commit: Y | feat(model): add ProviderOffer model, search radius config, and test infra

- [ ] 2. Generate Alembic migration for provider_offers table
  What to do / Must NOT do:
  - Run `cd backend && alembic revision --autogenerate -m "add_provider_offers_table"` or manually write migration
  - Migration must create table `provider_offers` with:
    - id (UUID, PK)
    - job_id (UUID, FK → jobs.id, NOT NULL, ON DELETE CASCADE)
    - provider_type (VARCHAR(32), NOT NULL)
    - provider_id (UUID, NOT NULL)
    - status (VARCHAR(32), default="pending")
    - expires_at (TIMESTAMPTZ, NOT NULL)
    - created_at (TIMESTAMPTZ, server_default=now())
    - UniqueConstraint("job_id", "provider_type", "provider_id")
  - down_revision must be "c1d2e3f4a5b6"
  - Must NOT drop or alter any existing tables/columns
  - Must NOT touch jobs table (scheduled_at already exists from Phase 2A)
  - Verify migration file name matches project pattern (descriptive snake_case)
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 5, 7
  References (executor has NO interview context - be exhaustive):
  - Existing migration pattern: `backend/migrations/versions/c1d2e3f4a5b6_add_scheduled_at_to_jobs.py` (uses op.execute for ALTER TABLE; new migration should use op.create_table)
  - Migration HEAD: `backend/migrations/versions/c1d2e3f4a5b6_add_scheduled_at_to_jobs.py:17` (down_revision = "c1d2e3f4a5b6") — new migration's down_revision = "c1d2e3f4a5b6"
  - Migration script: `backend/scripts/makemigration.sh`
  - Migrate script: `backend/scripts/migrate.sh`
  - ProviderOffer model (to match columns): `backend/app/models/provider_offer.py` (created in todo 1)
  Acceptance criteria (agent-executable):
  - Migration file exists: `ls backend/migrations/versions/*add_provider_offers*.py` returns 1 file
  - Migration HEAD correct: `cd backend && python -c "from alembic.script import ScriptDirectory; cfg=__import__('alembic.config').config.Config('alembic.ini'); s=ScriptDirectory.from_config(cfg); h=s.get_heads(); print('heads:', h); assert len(h) > 0"`
  - Migration upgrade runs clean: `cd backend && bash scripts/migrate.sh` (or equivalent dry-run) succeeds
  - Migration downgrade rolls back: verify downgrade drops the table
  QA scenarios (name the exact tool + invocation): happy + failure, Evidence .omo/evidence/task-2-phase-2b-provider-matching.txt
  - HAPPY: `cd backend && bash scripts/migrate.sh` — migration applies without error
  - HAPPY: verify migration file contains `op.create_table("provider_offers", ...)`, UniqueConstraint, and FKs
  - FAILURE: verify downgrade exists: `grep -q "op.drop_table" backend/migrations/versions/*add_provider_offers*.py`
  Commit: Y | feat(db): add provider_offers table migration

- [ ] 3. Update matching.py: add penalized, is_active, search radius to ALL 8 query variants
  What to do / Must NOT do:
  - Update `backend/app/services/matching.py`:
    - Import Settings: `from app.core.config import get_settings`
    - Get search_radius_m from settings
    - PostGIS path (nearest_mechanics, nearest_garages):
      - Add ST_DWithin geography filter: `AND ST_DWithin(geography(ST_SetSRID(ST_MakePoint(lon, lat), 4326)), geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326)), :radius_m)`
      - Add `m.penalized = false` or `mechanics.penalized = false` to WHERE
      - Change FROM `mechanics` to `mechanics m JOIN users u ON u.id = m.user_id` and add `AND u.is_active = true`
      - Same for garages
    - Fallback path (_fallback_mechanics, _fallback_garages):
      - Change FROM to include JOIN users: `FROM mechanics m JOIN users u ON u.id = m.user_id`
      - Add `AND m.penalized = false AND u.is_active = true` to WHERE
      - After computing dist_m = haversine_m(...), add radius check: `if dist_m > settings.search_radius_m: continue`
    - Must NOT remove or modify _score() function (line 51-53) — still used by fallback sorting
    - Must NOT remove or modify haversine_m() function (line 42-48)
    - Must NOT refactor raw SQL to ORM — maintain existing pattern
    - All 8 query variants must be updated:
      1. nearest_mechanics with issue_tag (PostGIS)
      2. nearest_mechanics without issue_tag (PostGIS)
      3. nearest_garages with issue_tag (PostGIS)
      4. nearest_garages without issue_tag (PostGIS)
      5. _fallback_mechanics with issue_tag
      6. _fallback_mechanics without issue_tag
      7. _fallback_garages with issue_tag
      8. _fallback_garages without issue_tag
  Parallelization: Wave 2 | Blocked by: 3 (config must exist for search_radius_m) | Blocks: 5
  References (executor has NO interview context - be exhaustive):
  - Full matching.py: `backend/app/services/matching.py:1-299` (all 8 query variants)
  - _score function: `backend/app/services/matching.py:51-53` (must PRESERVE)
  - haversine_m function: `backend/app/services/matching.py:42-48` (must PRESERVE)
  - Mechanic model with penalized: `backend/app/models/mechanic.py:29` (`penalized: Mapped[bool] = mapped_column(Boolean, default=False)`)
  - Garage model with penalized: `backend/app/models/garage.py:30` (`penalized: Mapped[bool] = mapped_column(Boolean, default=False)`)
  - User model with is_active: `backend/app/models/user.py:20` (`is_active: Mapped[bool] = mapped_column(Boolean, default=True)`)
  - Config with search_radius_m: `backend/app/core/config.py` (created in todo 1)
  - Settings import pattern: see `backend/app/services/job_service.py:7-9` (`from app.core.config import get_settings; settings = get_settings()`)
  - PostGIS query pattern: `backend/app/services/matching.py:167-215` (nearby_mechanics SQL — add ST_DWithin + JOIN here)
  - Fallback radius filter: add after `backend/app/services/matching.py:95` (after `dist_m = haversine_m(...)`)
  Acceptance criteria (agent-executable):
  - `cd backend && python -c "from app.services.matching import nearest_mechanics, nearest_garages, _fallback_mechanics, _fallback_garages, _score, haversine_m; print('all exports OK')"` — all functions still importable
  - Verify penalized filter in all 8 queries: `grep -c "penalized" backend/app/services/matching.py` >= 8 (one per variant)
  - Verify is_active filter: `grep -c "is_active" backend/app/services/matching.py` >= 4 (mechanics + garages × PostGIS + fallback)
  - Verify ST_DWithin: `grep -c "ST_DWithin" backend/app/services/matching.py` >= 2 (mechanics + garages PostGIS paths)
  - Verify _score() still exists: `grep -q "def _score" backend/app/services/matching.py`
  - Verify haversine_m() still exists: `grep -q "def haversine_m" backend/app/services/matching.py`
  QA scenarios (name the exact tool + invocation): happy + failure, Evidence .omo/evidence/task-3-phase-2b-provider-matching.txt
  - HAPPY: `cd backend && python -c "from app.services.matching import nearest_mechanics; import inspect; src=inspect.getsource(nearest_mechanics); assert 'penalized' in src; assert 'ST_DWithin' in src; print('PostGIS mechanics OK')"` 
  - HAPPY: Same for nearest_garages, _fallback_mechanics, _fallback_garages
  - FAILURE: verify fallback path treats distance > radius correctly — add test that provider 30km away is excluded when radius is 25km
  - FAILURE: verify _score importable and callable: `python -c "from app.services.matching import _score; assert _score(1000, 4.5, 0.5) > 0"`
  Commit: Y | feat(match): add penalized, is_active, search radius filters to all matching queries

- [ ] 4. Create offer_service.py with create_provider_offers() 
  What to do / Must NOT do:
  - Create `backend/app/services/offer_service.py` with:
    - `async def create_provider_offers(db: AsyncSession, job: Job) -> int`:
      - Returns number of ProviderOffer records created (0 if no eligible providers)
      - Acquires FOR UPDATE lock: `await db.execute(select(Job).where(Job.id == job.id, Job.status == "searching").with_for_update())`
      - If job not found or status != "searching": return 0 (already assigned/cancelled)
      - Get search_radius_m from settings
      - Call matching.nearest_mechanics(db, job.customer_lat, job.customer_lon, limit=10, issue_tag=job.issue_tag, radius_m=settings.search_radius_m)
        — note: nearest_mechanics will need updated signature to accept/expose radius_m
      - Same for nearest_garages
      - For each ranked mechanic (if request_type is mechanic or auto):
        - INSERT ProviderOffer(job_id=job.id, provider_type="mechanic", provider_id=m.id, expires_at=now+15min, status="pending")
        - Use `ON CONFLICT (job_id, provider_type, provider_id) DO NOTHING`
      - For each ranked garage (if request_type is garage or auto):
        - Same with provider_type="garage"
      - Batch insert using `db.execute_all()` or loop with `db.add()`
      - Return count of new offers created
  - Must NOT call assign_job_auto (that was Phase 2A behavior)
  - Must NOT set job.status = "assigned" (that's Phase 2C — provider acceptance)
  - Must NOT send WebSocket notifications
  - Password: use raw SQL for the INSERT with ON CONFLICT, or use SQLAlchemy ORM with merge/savepoint
  Parallelization: Wave 2 | Blocked by: 1 (ProviderOffer model), 2 (migration), 3 (matching.py updated) | Blocks: 5, 6
  References (executor has NO interview context - be exhaustive):
  - ProviderOffer model: `backend/app/models/provider_offer.py` (todo 1)
  - Updated matching.py: `backend/app/services/matching.py` (todo 3 — provides ranked providers with radius + eligibility)
  - FOR UPDATE pattern: `backend/app/services/job_service.py:70-72` (`select(Job).where(Job.id == job.id, Job.status == "searching").with_for_update()`)
  - Settings import: `backend/app/services/job_service.py:7-9` (pattern)
  - ON CONFLICT pattern: PostgreSQL dialect `from sqlalchemy.dialects.postgresql import Insert` then `insert(ProviderOffer).on_conflict_do_nothing()`
  - Existing matching call pattern: `backend/app/services/job_service.py:79-84` (nearest_mechanics/nearest_garages calls)
  - now+15min: `from datetime import datetime, timedelta, timezone; expires = datetime.now(timezone.utc) + timedelta(minutes=15)`
  Acceptance criteria (agent-executable):
  - `cd backend && python -c "from app.services.offer_service import create_provider_offers; print('imported OK')"` 
  - Function signature is `async def create_provider_offers(db, job) -> int` — confirms return type
  - Check FOR UPDATE usage: `grep -q "with_for_update" backend/app/services/offer_service.py`
  - Check ON CONFLICT: `grep -q "on_conflict_do_nothing\|ON CONFLICT" backend/app/services/offer_service.py`
  QA scenarios (name the exact tool + invocation): happy + failure, Evidence .omo/evidence/task-4-phase-2b-provider-matching.txt
  - HAPPY: unit test — mock matching to return 2 mechanics, call create_provider_offers, assert returns 2, assert 2 ProviderOffer rows in DB with correct job_id/provider_type/provider_id
  - HAPPY: verify expires_at ≈ now + 15min (within 1s tolerance)
  - FAILURE: call twice on same job — assert returns 0 on second call, assert no duplicate rows (ON CONFLICT works)
  - FAILURE: call on job with status="assigned" — assert returns 0, no offers created
  Commit: Y | feat(offer): add create_provider_offers service with dedup and lock

- [ ] 5. Wire create_provider_offers into job_service + Celery retry
  What to do / Must NOT do:
  - Update `backend/app/services/job_service.py`:
    - In `create_service_request` function (around line 201+):
      - Remove/replace the `assign_job_auto` call with `create_provider_offers`
      - After job creation + flush, call: `from app.services.offer_service import create_provider_offers; await create_provider_offers(db, job)`
      - If no offers created, schedule Celery retry (existing behavior): `retry_job_assignment.apply_async(args=[str(job.id)], countdown=45)`
      - If offers created, keep job.status = "searching"
      - Must NOT set job.status = "assigned" (that's still Phase 2C behavior)
    - Remove any lazy import of worker.retry_job_assignment if it was only used here
  - Update `backend/app/tasks/worker.py`:
    - In `retry_job_assignment` (line 28-47):
      - Replace `from app.services.job_service import assign_job_auto` with `from app.services.offer_service import create_provider_offers`
      - Call `await create_provider_offers(session, job)` instead of `await assign_job_auto(session, job)`
      - Keep same retry logic (only retry if job.status == "searching")
  - Must NOT remove `assign_job_auto` function from job_service.py — preserved as utility
  - Must NOT add any WebSocket notification
  Parallelization: Wave 3 | Blocked by: 4 (offer_service.py) | Blocks: 7
  References (executor has NO interview context - be exhaustive):
  - create_service_request: `backend/app/services/job_service.py:201-230` (currently ends with `await assign_job_auto(db, job)`)
  - retry_job_assignment: `backend/app/tasks/worker.py:27-47` (calls assign_job_auto)
  - Celery apply_async pattern: `backend/app/services/job_service.py:227-229` (existing retry scheduling)
  - assign_job_auto function: `backend/app/services/job_service.py:67-171` (must remain untouched)
  - offer_service.py: `backend/app/services/offer_service.py` (todo 4)
  Acceptance criteria (agent-executable):
  - `cd backend && python -c "from app.services.job_service import create_service_request, assign_job_auto; print('imported OK')"` — both still importable
  - `cd backend && python -c "from app.tasks.worker import retry_job_assignment; print('imported OK')"` — retry still importable
  - `cd backend && python -c "from app.services.offer_service import create_provider_offers; print('imported OK')"` — offer service importable
  - grep `assign_job_auto` in worker.py — should NOT appear (replaced with create_provider_offers)
  - grep `assign_job_auto` in job_service.py outside lines 67-171 — should NOT appear
  QA scenarios (name the exact tool + invocation): happy + failure, Evidence .omo/evidence/task-5-phase-2b-provider-matching.txt
  - HAPPY: Simulate create_service_request → verify ProviderOffer rows created in DB
  - HAPPY: Verify assign_job_auto still importable and callable: `python -c "from app.services.job_service import assign_job_auto; print(callable(assign_job_auto))"` = True
  - FAILURE: Verify no crash when matching returns zero providers (create_provider_offers returns 0, retry scheduled)
  - FAILURE: Verify retry_job_assignment with already-assigned job is no-op
  Commit: Y | feat(job): wire create_provider_offers into job creation and Celery retry

- [ ] 6. Add GET /providers/offers endpoint
  What to do / Must NOT do:
  - Add to `backend/app/api/v1/providers.py`:
    ```
    @router.get("/offers")
    @limiter.limit("30/minute")
    async def get_provider_offers(
        request: Request,
        db: DbSession,
        user: CurrentUser,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        status: str | None = Query(None, pattern="^(pending|expired)$"),
    ):
    ```
    - Auth guard: user.role must be "mechanic" or "garage" — 403 for customers/admins
    - Lookup mechanic/garage by user.id (same pattern as GET /earnings in providers.py:140-151)
    - Query ProviderOffer filtered by:
      - WHERE provider_type = user.role AND provider_id = mech.id/garage.id
      - Optional status filter (if provided)
      - Default: WHERE (expires_at IS NULL OR expires_at > NOW()) — show only active offers
      - ORDER BY created_at DESC
      - LIMIT: offset, limit
    - Response: list of offer dicts with job_id, provider_type, status, expires_at, created_at, and nested job summary
    - Must NOT allow customers to see offers
    - Must NOT create any offer acceptance mutation endpoints
  - Add response schema if needed (can inline as dict)
  - Must NOT change existing GET /nearby, PATCH /profile, PATCH /availability, GET /earnings
  Parallelization: Wave 3 | Blocked by: 1 (ProviderOffer model exists), 2 (migration applied) | Blocks: 7
  References (executor has NO interview context - be exhaustive):
  - ProviderOffer model: `backend/app/models/provider_offer.py` (todo 1)
  - Existing earnings endpoint for pattern: `backend/app/api/v1/providers.py:130-191` (role check, provider lookup by user_id)
  - Rate limit pattern: `backend/app/api/v1/providers.py:21-22` (@limiter.limit("30/minute"))
  - ProviderOffer query: `select(ProviderOffer).where(ProviderOffer.provider_type == ..., ProviderOffer.provider_id == ...)`
  - Job join for summary: `from app.models.job import Job` + join on job_id
  - Job response pattern: `backend/app/services/job_service.py:35-64` (job_response_dict — partial fields)
  Acceptance criteria (agent-executable):
  - `cd backend && python -c "from app.api.v1.providers import router; routes=[r.path for r in router.routes]; assert '/offers' in routes; print('/offers endpoint registered')"` — route registered
  - Role guard: `grep -q 'mechanic.*garage\|403' backend/app/api/v1/providers.py` for the offers handler
  - Rate limit: `grep -q 'limiter.limit.*30.*minute' backend/app/api/v1/providers.py` for offers
  QA scenarios (name the exact tool + invocation): happy + failure, Evidence .omo/evidence/task-6-phase-2b-provider-matching.txt
  - HAPPY: GET /providers/offers?limit=10&offset=0 with mechanic auth → 200 + list of offers
  - HAPPY: Verify expired offers excluded by default (created 16+ min ago, today — not returned)
  - HAPPY: Verify status=pending filter works
  - FAILURE: GET /providers/offers without auth → 401
  - FAILURE: GET /providers/offers with customer role → 403
  - FAILURE: GET /providers/offers with limit=0 → 422 (validation error)
  Commit: Y | feat(api): add GET /providers/offers endpoint

- [ ] 7. Write full test suite (28+ test scenarios)
  What to do / Must NOT do:
  - Create `backend/tests/test_matching.py`:
    - Test penalized=false filter (T1): create mechanic with penalized=True, assert excluded
    - Test user.is_active=false filter (T2): create mech with user.is_active=False, assert excluded via JOIN
    - Test search radius PostGIS (T3): create mech beyond 25km, assert excluded
    - Test within radius included (T4): create mech within 1km, assert included
    - Test issue_tag filtering still works (T5), available=false excluded (T6), verified=false excluded (T7)
    - Test fallback path honors radius (T15): mock PostGIS failure, assert haversine distance filter works
    - Test fallback honors penalized (T16) and is_active (T17)
  - Create `backend/tests/test_offers.py`:
    - Test create_provider_offers creates records (T8): 2 eligible mechs → 2 offers created
    - Test duplicate prevention (T9): call twice → 0 on second call, no duplicate rows
    - Test status != "searching" → no-op (T10): set job status to "assigned", assert 0 offers
    - Test expires_at ≈ now+15min (T11): within tolerance
    - Test request_type scoping: mechanic only (T12), garage only (T13), auto = both (T14)
  - Create `backend/tests/test_api_offers.py`:
    - Test 401 without auth (T18)
    - Test 403 for customer role (T19)
    - Test mechanic sees own offers only (T20)
    - Test garage sees own offers only (T21)
    - Test response shape (T22): expected fields present
    - Test expired offers excluded (T23)
  - Create `backend/tests/test_integration.py`:
    - Test create_service_request creates ProviderOffers (T24)
    - Test assign_job_auto still callable (T26)
  - Create `backend/tests/test_config.py`:
    - Test search_radius_km default 25 (T27)
    - Test search_radius_m = 25000 (T28)
  - Must NOT require running PostgreSQL — use SQLite for tests or env-override
  - Must NOT make real HTTP calls — use httpx.AsyncClient with ASGI
  - Must NOT modify any production code to pass tests (TDD/honest testing)
  - Must use pytest-asyncio for async test functions
  Parallelization: Wave 4 | Blocked by: 5 (wiring done), 6 (endpoint exists) | Blocks: final verification wave
  References (executor has NO interview context - be exhaustive):
  - conftest.py: `backend/tests/conftest.py` (created in todo 1 — has fixtures for test DB, session, client, factories)
  - matching functions: `backend/app/services/matching.py` (all 8 query variants)
  - offer service: `backend/app/services/offer_service.py` (create_provider_offers)
  - providers endpoint: `backend/app/api/v1/providers.py` (GET /offers handler)
  - httpx.AsyncClient: `from httpx import AsyncClient, ASGITransport` + `transport = ASGITransport(app=app)` + `async with AsyncClient(transport=transport, base_url="http://test") as client`
  - Job model: `backend/app/models/job.py` (for creating test jobs)
  - Mechanic/Garage models: `backend/app/models/mechanic.py`, `backend/app/models/garage.py`
  - User model: `backend/app/models/user.py` (is_active flag)
  Acceptance criteria (agent-executable):
  - `cd backend && python -m pytest tests/ -x -v --tb=short` — ALL 28+ tests PASS
  - `cd backend && python -m pytest tests/ --co --tb=short -q | tail -n 5` — shows test count >= 28
  - Each test has a docstring explaining what it verifies
  QA scenarios (name the exact tool + invocation): happy + failure, Evidence .omo/evidence/task-7-phase-2b-provider-matching.txt
  - HAPPY: Full test suite green: `cd backend && python -m pytest tests/ -x -v 2>&1 | tail -20`
  - FAILURE: Test each filter independently (penalized, is_active, radius, available, verified) — each test creates specific state and asserts exclusion
  - FAILURE: Test dedup by calling create_provider_offers twice, assert no duplicate rows
  Commit: Y | test: add 28+ test scenarios for provider matching

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
  - Verify all 7 todos implemented
  - Verify all Must NOT have rules followed (no acceptance endpoints, no frontend, no refactoring, no assign_job_auto removal)
  - Evidence: diff check + grep for forbidden patterns
- [ ] F2. Code quality review
  - Verify _score() and haversine_m() preserved in matching.py
  - Verify no hardcoded secrets introduced
  - Verify no TODO/FIXME stubs in production code
  - Evidence: grep check + code review
- [ ] F3. Real manual QA
  - Verify all 28+ test scenarios pass: `cd backend && python -m pytest tests/ -x -v`
  - Verify migration up + down works: `cd backend && bash scripts/migrate.sh` then downgrade
  - Verify `cd backend && python -c "from app.models import ProviderOffer; from app.services import matching, offer_service; from app.tasks.worker import retry_job_assignment; print('ALL IMPORT OK')"`
  - Verify assign_job_auto still importable and callable
  - Evidence: test run logs, migration logs, import check logs
- [ ] F4. Scope fidelity
  - Verify no changes to: matching_routes.py, jobs.py (except create_service_request), frontend
  - Verify no new endpoints beyond GET /providers/offers
  - Verify ProviderOffer model does NOT collide with ClutchDOffer
  - Evidence: git diff --stat check

## Commit strategy
1. `feat(model): add ProviderOffer model, search radius config, and test infra` (todo 1)
2. `feat(db): add provider_offers table migration` (todo 2)
3. `feat(match): add penalized, is_active, search radius filters to matching queries` (todo 3)
4. `feat(offer): add create_provider_offers service with dedup and lock` (todo 4)
5. `feat(job): wire create_provider_offers into job creation and Celery retry` (todo 5)
6. `feat(api): add GET /providers/offers endpoint` (todo 6)
7. `test: add 28+ test scenarios for provider matching` (todo 7)

## Success criteria
1. All 28+ tests pass: `cd backend && python -m pytest tests/ -x -v` returns 0 exit code
2. Migration applies and rolls back cleanly
3. ProviderOffer model with UNIQUE constraint exists and is registered
4. Matching.py filters penalized, is_active, and search radius in all 8 query variants
5. create_provider_offers() creates offers with ON CONFLICT dedup and FOR UPDATE lock
6. create_service_request creates offers (not auto-assignment)
7. Celery retry uses create_provider_offers (no more assign_job_auto call from worker)
8. GET /providers/offers returns pending offers for authenticated providers only
9. assign_job_auto preserved as callable utility
10. No frontend changes, no acceptance endpoints, no WebSocket additions
