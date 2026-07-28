---
slug: phase-2b-provider-matching
status: drafting
intent: clear
review_required: false
pending-action: write .omo/plans/phase-2b-provider-matching.md
approach: Add ProviderOffer model + migration, update matching.py with penalized/is_active/radius filters, create offer creation service, add GET /providers/offers endpoint, update job_service.create_service_request and Celery retry to use new flow, add tests
---

# Draft: phase-2b-provider-matching

## Components (topology ledger)
<!-- id | outcome (one line) | status: active|deferred | evidence path -->
1. ProviderOffer model + migration | New ORM table with UNIQUE(job_id, provider_type, provider_id) | active | backend/app/models/
2. Search radius config | Add search_radius_km to Settings + postgis distance filter | active | backend/app/core/config.py
3. Matching eligibility update | Add penalized=false, user.is_active=true, ST_DWithin radius filter | active | backend/app/services/matching.py
4. Offer creation service | create_provider_offers() in matching.py or new offers.py | active | backend/app/services/
5. Celery retry + job_service integration | Replace assign_job_auto call in create_service_request, update worker.py | active | backend/app/services/job_service.py, backend/app/tasks/worker.py
6. GET /providers/offers endpoint | List offers for current provider | active | backend/app/api/v1/providers.py
7. Test infra + tests | pytest setup, 13+ tests | active | backend/tests/

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
- Test framework: pytest with pytest-asyncio (project standard). No existing test infra - will create from scratch.
- Search radius: 25km default via config. Reasonable for ride-hailing/marketplace use case.
- ProviderOffer expires_at: 15 minutes. Matches typical offer window.
- Celery retry: existing retry_job_assignment updated to call create_provider_offers instead of assign_job_auto. Existing assign_job_auto preserved for admin manual assignment.
- Alembic migration name: `add_provider_offers_and_search_radius`
- Project uses async SQLAlchemy with PostgreSQL/PostGIS

## Findings (cited - path:lines)

### Matching.py gaps (backend/app/services/matching.py):
- Missing penalized filter on mechanics (line 77: `WHERE verified = true AND available = true` - no `penalized = false`)
- Missing penalized filter on garages (line 124: `WHERE verified = true` - no `penalized = false`)
- Missing user.is_active join on both mechanics and garages queries
- Missing search radius filter (ST_DWithin or haversine-based distance limit)
- No ProviderOffer creation logic exists anywhere
- _score() function (line 51-53) must be preserved - used by assign_job_auto

### Job service (backend/app/services/job_service.py):
- create_service_request (line 201+) currently calls assign_job_auto at end
- assign_job_auto (line 67-171) does direct matching + assignment (Phase 2C behavior - should become offers)
- Must preserve assign_job_auto for admin manual assignment

### Worker (backend/app/tasks/worker.py):
- retry_job_assignment (line 28-47) calls assign_job_auto directly
- Must update to call create_provider_offers instead

### Models:
- Mechanic has penalized (bool, default=False) at backend/app/models/mechanic.py:29
- Garage has penalized (bool, default=False) at backend/app/models/garage.py:30
- User has is_active (bool, default=True) at backend/app/models/user.py:20
- No ProviderOffer model exists

### Config (backend/app/core/config.py):
- No search_radius_km field - needs to be added

### Providers API (backend/app/api/v1/providers.py):
- Existing routes: GET /nearby, PATCH /profile, PATCH /availability, GET /earnings
- No GET /offers endpoint exists

### Migration chain:
- HEAD migration: c1d2e3f4a5b6 ("add scheduled_at to jobs") - down_revision: b0c1d2e3f4a5 (backend/migrations/versions/c1d2e3f4a5b6_add_scheduled_at_to_jobs.py:17)
- New migration down_revision must be: c1d2e3f4a5b6

### Test infra:
- Zero test files exist (AGENTS.md:57: "No tests — zero test files across the entire project")

## Decisions (with rationale)

1. **Create ProviderOffer model in existing provider_offer.py** - Follows project pattern (one model per file)
2. **Keep assign_job_auto() for admin manual assignment** - Don't break existing admin flow; just don't call it from create_service_request anymore
3. **Replace assign_job_auto call in create_service_request with create_provider_offers** - Phase 2B change: create offers instead of auto-assigning
4. **Add search_radius_km and search_radius_m to Settings** - Configurable at deploy time
5. **Filter used: penalized=false + user join for is_active=true + ST_DWithin** - Three new eligibility filters
6. **Test framework: pytest + pytest-asyncio + httpx** - Standard FastAPI testing stack
7. **Use raw SQL in matching.py (existing pattern)** - Don't refactor to ORM; maintain PostGIS + fallback pattern

## Scope IN

1. ProviderOffer model + Alembic migration
2. Search radius config (search_radius_km) in Settings
3. Update matching.py: add penalized=false, user.is_active=true, ST_DWithin search radius to all 8 query variants
4. create_provider_offers() function: find eligible providers → create ProviderOffer records
5. Update job_service.create_service_request to call create_provider_offers instead of assign_job_auto
6. Update Celery retry_job_assignment to call create_provider_offers
7. GET /providers/offers endpoint for providers to see their pending offers
8. pytest test infrastructure + 13+ tests covering matching eligibility, offer creation, duplicate prevention, endpoint

## Scope OUT (Must NOT have)

1. Provider acceptance flow (Phase 2C) - no offer acceptance/rejection endpoints
2. Live tracking, chat, quotes, payments, WebSocket notifications
3. Any frontend changes
4. Removing assign_job_auto() - preserve for admin
5. Any refactoring of existing matching.py patterns (raw SQL, PostGIS + haversine fallback)

## Metis gap analysis findings
**Critical issues found:**
- C1: Fallback queries lack JOIN for user.is_active → crash on Render (no PostGIS fallback path)
- C2: Fallback path has no radius filter → inconsistent with PostGIS path
- C3: Celery retry will create duplicate ProviderOffer without dedup strategy
- C4: No test infrastructure → 13+ tests require full test setup (pytest, conftest, fixtures, test DB)

**High issues to incorporate:**
- H1: ST_DWithin must use geography type, keep ST_Distance for ORDER BY
- H2: create_provider_offers() → new file `services/offer_service.py`
- H3: GET /providers/offers needs pagination, status filter, sort, auth scope, rate limit
- H4: create_provider_offers needs FOR UPDATE lock (race condition with Celery retry)
- H5: Missing offer expiry cleanup → add WHERE expires_at > now() to query

**All findings incorporated into plan todos.**

## Approval gate
status: approved
approved_at: 2026-07-26
plan_path: .omo/plans/phase-2b-provider-matching.md
review_required: false
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
