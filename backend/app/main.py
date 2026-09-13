import json
import logging
import os
import time
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from uuid import UUID
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api.v1.router import api_router
from app.api.v1.token import router as token_router
from app.api.deps import get_current_user, DbSession
from app.core.config import get_settings
from app.core.firebase import init_firebase
from app.core.limiter import limiter as app_limiter
from app.core.security import decode_token, is_token_blacklisted
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.ws.manager import manager, push_location_update

logger = logging.getLogger(__name__)

settings = get_settings()

# ---- WebSocket safety limits ----
MAX_WS_MESSAGE_SIZE = 4096         # bytes — reject anything larger
WS_MSG_RATE_LIMIT = 10             # max messages per second per connection
LOCATION_DB_INTERVAL_SEC = 30      # throttle how often we persist GPS to DB
_last_db_persist: dict[str, float] = {}  # user_id -> timestamp of last DB write


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.upload_dir).resolve()
    init_firebase()  # Initialize FCM (graceful skip if unconfigured)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.state.limiter = app_limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return _error_response(
        status_code=429,
        error="Too Many Requests",
        detail="Rate limit exceeded. Please slow down.",
        request_id=request.state.request_id,
    )

app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"^(capacitor://|http://|https://)(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request ID tracking ──────────────────────────────────────
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        request_id = uuid.uuid4()
        request.state.request_id = str(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request_id)
        return response


# ── Request body size limit (10 MB) ──────────────────────────
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10_000_000:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "Request Too Large",
                    "detail": "Request too large (max 10MB)",
                    "request_id": request.state.request_id,
                },
            )
        return await call_next(request)


app.add_middleware(RequestIDMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(token_router, prefix=settings.api_prefix)

static_dir = Path(settings.upload_dir)
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/uploads", StaticFiles(directory=str(static_dir)), name="static_uploads")


# ---- Security headers middleware ----
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(self)"
    # Removed HSTS header from application level; usually handled by Nginx/Traefik in prod.
    return response


# ---- Standardized error response format ----
def _error_response(status_code: int, error: str, detail: str, request_id: str = "") -> JSONResponse:
    content: dict = {
        "error": error,
        "detail": detail,
        "status_code": status_code,
    }
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(
        status_code=status_code,
        content=content,
    )


# ---- Override FastAPI's default 422 validation error ----
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return _error_response(
        status_code=exc.status_code,
        error=HTTPStatus(exc.status_code).phrase,
        detail=detail,
        request_id=request.state.request_id,
    )


# ---- Catch-all for unhandled exceptions ----
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled server error: %s", exc, exc_info=True)
    if settings.debug:
        return _error_response(
            status_code=500,
            error="Internal Server Error",
            detail=str(exc),
            request_id=request.state.request_id,
        )
    return _error_response(
        status_code=500,
        error="Internal Server Error",
        detail="Internal server error",
        request_id=request.state.request_id,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/chat/history/{job_id}")
async def chat_history(job_id: UUID, user: User = Depends(get_current_user), db: DbSession = None):
    """Chat history for a job — customer or assigned provider only."""
    return await _chat_history_impl(job_id, user, db=db)


async def _chat_history_impl(job_id: UUID, user, db=None):
    from app.models.chat import ChatMessage
    from app.models.job import Job

    if user is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Not authenticated")

    owns_db = db is None
    if owns_db:
        db = AsyncSessionLocal()
    try:
        jr = await db.execute(select(Job).where(Job.id == job_id))
        job_row = jr.scalar_one_or_none()
        if not job_row:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Job not found")

        is_customer = job_row.user_id == user.id
        is_provider = False
        if job_row.assigned_type == "mechanic" and job_row.assigned_mechanic_id:
            from app.models.mechanic import Mechanic

            r = await db.execute(
                select(Mechanic.id).where(
                    Mechanic.id == job_row.assigned_mechanic_id,
                    Mechanic.user_id == user.id,
                )
            )
            is_provider = r.scalar_one_or_none() is not None
        elif job_row.assigned_type == "garage" and job_row.assigned_garage_id:
            from app.models.garage import Garage

            r = await db.execute(
                select(Garage.id).where(
                    Garage.id == job_row.assigned_garage_id,
                    Garage.user_id == user.id,
                )
            )
            is_provider = r.scalar_one_or_none() is not None

        if not (is_customer or is_provider or user.is_superuser):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Not a participant of this job")

        cr = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.job_id == job_id)
            .order_by(ChatMessage.created_at.asc())
        )
        messages = cr.scalars().all()
        return {
            "messages": [
                {
                    "id": str(m.id),
                    "jobId": str(m.job_id),
                    "senderId": str(m.sender_id),
                    "senderRole": m.sender_role,
                    "text": m.text,
                    "imageUrl": m.image_url,
                    "createdAt": m.created_at.isoformat() if m.created_at else None,
                }
                for m in messages
            ]
        }
    finally:
        if owns_db:
            await db.close()





async def _authenticate_ws(websocket: WebSocket) -> User | None:
    """Authenticate a WebSocket connection via Sec-WebSocket-Protocol header."""
    protocol_header = websocket.headers.get("sec-websocket-protocol")
    logger.info("[WS AUTH] all headers: %s", dict(websocket.headers))
    logger.info("[WS AUTH] sec-websocket-protocol header: protocol_header=%s", protocol_header)
    if not protocol_header:
        logger.warning("[WS AUTH] REJECT: no protocol header")
        await websocket.close(code=4401)
        return None
    token = protocol_header.split(",")[0].strip()
    logger.info("[WS AUTH] protocol split result: first_token_len=%d, first_30_chars=%s", len(token), repr(token[:30]))
    if not token:
        logger.warning("[WS AUTH] REJECT: empty token after split")
        await websocket.close(code=4401)
        return None
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        logger.warning("[WS AUTH] REJECT: decode_token returned %s", payload)
        await websocket.close(code=4401)
        return None
    logger.info("[WS AUTH] decoded OK — sub=%s, type=%s", payload.get("sub"), payload.get("type"))
    jti = payload.get("jti")
    if jti and await is_token_blacklisted(jti):
        logger.warning("[WS AUTH] REJECT: token blacklisted (jti=%s)", jti)
        await websocket.close(code=4401)
        return None
    try:
        uid = UUID(payload["sub"])
    except ValueError:
        logger.warning("[WS AUTH] REJECT: invalid UUID sub=%s", payload.get("sub"))
        await websocket.close(code=4401)
        return None
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.id == uid, User.is_active.is_(True)))
        user = r.scalar_one_or_none()
    if not user:
        logger.warning("[WS AUTH] REJECT: user not found or inactive (uid=%s)", uid)
        await websocket.close(code=4401)
        return None
    logger.info("[WS AUTH] ACCEPT — user=%s role=%s", user.id, user.role)
    return user


@app.websocket("/ws")
async def websocket_user(websocket: WebSocket):
    user = await _authenticate_ws(websocket)
    if not user:
        return

    await manager.connect_user(str(user.id), websocket)
    # Per-connection rate limiter
    msg_timestamps: list[float] = []

    try:
        while True:
            raw = await websocket.receive_text()

            # ---- Size guard ----
            if len(raw) > MAX_WS_MESSAGE_SIZE:
                continue

            # ---- Rate guard ----
            now = time.monotonic()
            msg_timestamps = [t for t in msg_timestamps if now - t < 1.0]
            if len(msg_timestamps) >= WS_MSG_RATE_LIMIT:
                continue  # Silently drop — client is flooding
            msg_timestamps.append(now)

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "CHAT_MESSAGE":
                from app.models.job import Job as _ChatJob
                from app.models.mechanic import Mechanic as _ChatMechanic
                from app.models.garage import Garage as _ChatGarage

                payload = msg.get("payload") or {}
                raw_job_id = payload.get("jobId") or payload.get("job_id")
                text = payload.get("text")
                image_url = payload.get("imageUrl") or payload.get("image_url")
                if not raw_job_id or (not text and not image_url):
                    continue
                try:
                    job_uuid = UUID(str(raw_job_id))
                except ValueError:
                    continue
                text = (text or "")[:4000]

                # Persist + relay. Verify participation: job's customer or assigned provider.
                async with AsyncSessionLocal() as cdb:
                    from app.models.chat import ChatMessage

                    jr2 = await cdb.execute(select(_ChatJob).where(_ChatJob.id == job_uuid))
                    job_row = jr2.scalar_one_or_none()
                    if not job_row:
                        continue
                    is_customer = job_row.user_id == user.id
                    is_provider = (job_row.assigned_type == "mechanic" and job_row.assigned_mechanic_id is not None and (
                        await cdb.execute(
                            select(_ChatMechanic.id).where(
                                _ChatMechanic.id == job_row.assigned_mechanic_id,
                                _ChatMechanic.user_id == user.id,
                            )
                        )).scalar_one_or_none() is not None) or (
                        job_row.assigned_type == "garage" and job_row.assigned_garage_id is not None and (
                            await cdb.execute(
                                select(_ChatGarage.id).where(
                                    _ChatGarage.id == job_row.assigned_garage_id,
                                    _ChatGarage.user_id == user.id,
                                )
                            )).scalar_one_or_none() is not None)
                    if not (is_customer or is_provider or user.is_superuser):
                        continue

                    chat_msg = ChatMessage(
                        job_id=job_uuid,
                        sender_id=user.id,
                        sender_role=user.role,
                        text=text or None,
                        image_url=image_url,
                    )
                    cdb.add(chat_msg)
                    await cdb.commit()
                    await cdb.refresh(chat_msg)

                outbound = {
                    "type": "CHAT_MESSAGE",
                    "payload": {
                        "id": str(chat_msg.id),
                        "jobId": str(job_uuid),
                        "senderId": str(user.id),
                        "senderRole": user.role,
                        "text": text,
                        "imageUrl": image_url,
                        "createdAt": chat_msg.created_at.isoformat() if chat_msg.created_at else None,
                    },
                }
                # Deliver to the other participant (and echo to sender for ID sync)
                await manager.send_json_to_user(str(job_row.user_id), outbound)
                if job_row.assigned_type == "mechanic" and job_row.assigned_mechanic_id:
                    async with AsyncSessionLocal() as cdb2:
                        pr = await cdb2.execute(select(_ChatMechanic.user_id).where(_ChatMechanic.id == job_row.assigned_mechanic_id))
                        provider_uid = pr.scalar_one_or_none()
                    if provider_uid:
                        await manager.send_json_to_user(str(provider_uid), outbound)
                elif job_row.assigned_type == "garage" and job_row.assigned_garage_id:
                    async with AsyncSessionLocal() as cdb3:
                        pr = await cdb3.execute(select(_ChatGarage.user_id).where(_ChatGarage.id == job_row.assigned_garage_id))
                        provider_uid = pr.scalar_one_or_none()
                    if provider_uid:
                        await manager.send_json_to_user(str(provider_uid), outbound)
                continue

            if msg.get("type") == "MECHANIC_LOCATION" and user.role == "mechanic":
                lat, lon = msg.get("lat"), msg.get("lon")
                if lat is None or lon is None:
                    continue
                try:
                    lat_f, lon_f = float(lat), float(lon)
                except (ValueError, TypeError):
                    continue

                # Always push real-time location to connected customers
                from app.models.job import Job
                from app.models.mechanic import Mechanic

                async with AsyncSessionLocal() as db:
                    mr = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
                    mech = mr.scalar_one_or_none()
                    if not mech:
                        continue

                    # Push live location over WebSocket immediately (no DB)
                    jr = await db.execute(
                        select(Job).where(
                            Job.assigned_mechanic_id == mech.id,
                            Job.status.in_(("assigned", "en_route", "in_progress")),
                        )
                    )
                    for job in jr.scalars().all():
                        await push_location_update(str(job.user_id), str(job.id), [lat_f, lon_f])

                    # Throttle DB persistence to reduce load
                    uid_str = str(user.id)
                    last_persist = _last_db_persist.get(uid_str, 0.0)
                    if time.time() - last_persist >= LOCATION_DB_INTERVAL_SEC:
                        mech.lat = lat_f
                        mech.lon = lon_f
                        await db.commit()
                        _last_db_persist[uid_str] = time.time()

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect_user(str(user.id), websocket)


@app.websocket("/ws/tracking/{job_id}")
async def websocket_tracking(websocket: WebSocket, job_id: str):
    user = await _authenticate_ws(websocket)
    if not user:
        return
    try:
        jid = UUID(job_id)
    except ValueError:
        await websocket.close(code=4400)
        return
    from app.services.job_service import get_job_for_user

    async with AsyncSessionLocal() as db:
        job = await get_job_for_user(db, jid, user)
    if not job:
        await websocket.close(code=4403)
        return
    await manager.connect_job(job_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            # Rate/size guard on tracking socket too
            if len(raw) > MAX_WS_MESSAGE_SIZE:
                continue
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect_job(job_id, websocket)
