# ClutchD-Backend — Ride-hailing marketplace backend

## OVERVIEW
FastAPI + PostGIS + Celery + Redis backend for a ride-hailing/marketplace platform, deployed on Render.com.

## STRUCTURE
```
backend/
├── app/
│   ├── api/v1/          # 20+ route modules (auth, admin, jobs, payments, marketplace...)
│   ├── core/            # config, security (JWT), redis_client, rate limiter
│   ├── db/              # async SQLAlchemy session, Base
│   ├── models/          # 14+ ORM models (user, job, vehicle, marketplace, payment...)
│   ├── schemas/         # 16 Pydantic request/response schemas
│   ├── services/        # auth_service, job_service, matching, payout
│   ├── tasks/           # Celery worker (redis broker)
│   ├── ws/              # WebSocket connection manager
│   ├── main.py          # FastAPI app factory, lifespan, CORS, routers
│   └── seed_admin_data.py
├── migrations/          # Alembic versions
├── scripts/             # bootstrap_db.py, makemigration.sh, migrate.sh
├── Dockerfile           # API image
├── Dockerfile.worker    # Celery worker image
└── requirements.txt
docker-compose.yml       # 4 services: db (postgis), redis, api (port 8001), worker
render.yaml              # Render.com deploy (free tier, managed PG + Redis)
Procfile                 # web + worker process types
.github/workflows/ci.yml # pip install + compileall only
```

## WHERE TO LOOK
| Need | File |
|------|------|
| Routes | `backend/app/api/v1/*.py` (router.py aggregates) |
| DB models | `backend/app/models/*.py` |
| Auth flow | `core/security.py`, `services/auth_service.py`, `api/v1/auth.py` |
| Job logic | `services/job_service.py`, `api/v1/jobs.py` |
| Config | `core/config.py` (pydantic-settings) |
| Migrations | `backend/migrations/versions/` |
| Docker dev | `docker-compose.yml` |
| Deploy | `render.yaml` |

## CONVENTIONS
- Async SQLAlchemy with asyncpg driver
- Alembic for migrations (run via `makemigration.sh` / `migrate.sh`)
- UVicorn on port 8000 (mapped to 8001 in Docker)
- Celery with Redis broker for background tasks
- JWT auth via python-jose + passlib/bcrypt
- Stripe + Razorpay for payments
- PostGIS for geospatial queries (location-based matching)
- Google OAuth via authlib
- `.env.example` tracks all env vars; Docker uses `env.docker.example`

## ANTI-PATTERNS
1. **Hardcoded JWT secret** in `docker-compose.yml` (default fallback value) — dev only, but risky in version control
2. **Duplicate codebase** at `ClutchD/ClutchD-Backend/` — two copies diverge over time
3. **No tests** — zero test files across the entire project
4. **Minimal CI** — `pip install` + `compileall` only; no lint, no type check, no test run
5. **Committed AGENTS.md** was a generic memory loader template, not project-specific

## COMMANDS
```bash
# Start full stack (Docker)
docker compose up --build

# Run API directly (outside Docker)
cd backend && uvicorn app.main:app --reload --port 8000

# Alembic
cd backend && bash scripts/makemigration.sh "<message>"   # auto-generate
cd backend && bash scripts/migrate.sh                      # apply

# Celery worker
cd backend && celery -A app.tasks.worker.celery_app worker --loglevel=info

# Bootstrap DB (seed data)
cd backend && python scripts/bootstrap_db.py
```
