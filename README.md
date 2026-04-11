# MechOnCall — Backend (startup-backup)

Production-oriented FastAPI service for the **MechOnCall** on-demand mechanic platform. It mirrors the API shape expected by the frontend in `the-startup` (`/api` prefix, `/auth/signup`, `/service/request`, `/providers/nearby`, WebSocket at `/ws`) and adds the full spec: PostGIS matching, Redis, Celery, Stripe/Razorpay hooks, admin APIs, and job-scoped WebSockets.

## Stack

- **FastAPI** (async) + **Pydantic v2**
- **PostgreSQL + PostGIS** (geography distance queries)
- **Redis** (pricing multiplier cache; optional pub/sub extension point)
- **Celery** (retry job assignment, notification stub)
- **JWT** auth, **bcrypt** passwords, optional **Google ID token** login
- **WebSockets**: `GET ws://host/ws?token=...` (user channel + mechanic location updates) and `ws://host/ws/tracking/{job_id}?token=...`

## Quick start (Docker)

From this directory:

```bash
docker compose up --build
```

- REST: `http://localhost:8000/api/...`
- Health: `http://localhost:8000/health`
- OpenAPI: `http://localhost:8000/docs`

The API container runs `scripts/bootstrap_db.py` on start (creates PostGIS + tables + seed users if missing).

### Seed accounts

| Email | Password | Role |
| --- | --- | --- |
| `admin@mechoncall.com` | `AdminChangeMe!` | admin |
| `customer@demo.com` | `demo123456` | customer |
| `mechanic@demo.com` | `demo123456` | mechanic (verified, near Coimbatore) |
| `garage@demo.com` | `demo123456` | garage (verified) |

## Local development (without Docker)

1. Install PostGIS locally and create DB `mechoncall` (or adjust URLs).
2. `cd backend && python -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and edit.
5. `python scripts/bootstrap_db.py`
6. `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
7. Celery (optional): `celery -A app.tasks.worker.celery_app worker --loglevel=info`

### Frontend env

Point the Next app at this API (as in `the-startup`):

- `NEXT_PUBLIC_API_URL=http://localhost:8000/api`
- `NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws`

Uploaded files are served under `http://localhost:8000/static/uploads/...` (set full URL in the client if needed).

## API map (high level)

| Area | Paths |
| --- | --- |
| Auth | `POST /api/auth/login`, `/signup`, `/register/customer|mechanic|garage`, `/oauth/google`, `/logout` |
| Service (frontend) | `POST /api/service/request`, `PATCH .../status`, `POST .../complete`, `POST .../cancel` |
| Jobs (spec) | `POST /api/jobs/create`, `POST /api/jobs/assign`, `GET /api/jobs/status/{id}` |
| Matching | `GET /api/mechanics/nearby`, `/api/garages/nearby`, `GET /api/providers/nearby` |
| Garage | `POST /api/garage/add-mechanic`, `GET /api/garage/mechanics` |
| Reviews | `POST /api/reviews`, `GET /api/ratings/{entity_id}?entity_type=mechanic\|garage` |
| Payments | `POST /api/payments/create`, `POST /api/payments/verify` |
| Admin | `GET /api/admin/users`, `PATCH .../mechanics/{id}/verify`, `PATCH .../garages/{id}/verify`, analytics, disputes, pricing (Redis) |
| Uploads | `POST /api/uploads` (multipart) |

## Security notes

- Set a strong `JWT_SECRET_KEY` in production.
- Configure `CORS_ORIGINS` explicitly.
- Stripe/Razorpay keys are optional until you enable payments.
- Change seed passwords before any shared deployment.

## Layout

```
backend/
  app/
    main.py           # FastAPI app, CORS, static uploads, WebSockets
    api/v1/           # Routers
    core/             # config, security, limiter, redis
    db/               # async engine + session
    models/           # SQLAlchemy models
    schemas/          # Pydantic
    services/         # auth, jobs, matching
    tasks/worker.py   # Celery app + tasks
    ws/manager.py     # WebSocket fan-out
  scripts/bootstrap_db.py
  requirements.txt
  Dockerfile
docker-compose.yml
```

The frontend project is **not** modified here; it remains the reference for request/response shapes.
