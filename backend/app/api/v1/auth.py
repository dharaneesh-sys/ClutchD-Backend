import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.deps import DbSession
from app.core.limiter import limiter
from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token, hash_password
from app.models.user import User
from app.models.mechanic import Mechanic
from app.models.garage import Garage
from app.schemas.auth import (
    CustomerRegister,
    GarageRegister,
    GoogleOAuthRequest,
    LoginRequest,
    MechanicRegister,
    MessageResponse,
    SignupPayload,
    TokenResponse,
    ForgotPasswordRequest,
    PasswordResetRequest,
)
from app.services import auth_service
from app.services.auth_service import AuthError
from app.services.user_payload import user_to_frontend_dict
from app.api.v1.token import set_refresh_cookie, clear_refresh_cookie
from app.core.redis_client import get_redis
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_exc(e: AuthError) -> HTTPException:
    return HTTPException(status_code=e.code, detail=e.message)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("30/minute")
async def login_route(request: Request, body: LoginRequest, db: DbSession):
    try:
        token, user_payload, user_id = await auth_service.login(db, body.email, body.password)
        refresh = create_refresh_token(user_id)
        response = JSONResponse(content=TokenResponse(token=token, user=user_payload).model_dump())
        set_refresh_cookie(response, refresh)
        return response
    except AuthError as e:
        raise _auth_exc(e) from e


@router.post("/signup", response_model=TokenResponse)
@limiter.limit("15/minute")
async def signup_route(request: Request, body: SignupPayload, db: DbSession):
    try:
        token, user_payload, user_id = await auth_service.signup_from_payload(db, body)
        refresh = create_refresh_token(user_id)
        response = JSONResponse(content=TokenResponse(token=token, user=user_payload).model_dump())
        set_refresh_cookie(response, refresh)
        return response
    except AuthError as e:
        raise _auth_exc(e) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/register/customer", response_model=TokenResponse)
@limiter.limit("15/minute")
async def register_customer(request: Request, body: CustomerRegister, db: DbSession):
    try:
        token, user_payload, user_id = await auth_service.register_customer(db, body)
        refresh = create_refresh_token(user_id)
        response = JSONResponse(content=TokenResponse(token=token, user=user_payload).model_dump())
        set_refresh_cookie(response, refresh)
        return response
    except AuthError as e:
        raise _auth_exc(e) from e


@router.post("/register/mechanic", response_model=TokenResponse)
@limiter.limit("15/minute")
async def register_mechanic(request: Request, body: MechanicRegister, db: DbSession):
    try:
        token, user_payload, user_id = await auth_service.register_mechanic(db, body)
        refresh = create_refresh_token(user_id)
        response = JSONResponse(content=TokenResponse(token=token, user=user_payload).model_dump())
        set_refresh_cookie(response, refresh)
        return response
    except AuthError as e:
        raise _auth_exc(e) from e


@router.post("/register/garage", response_model=TokenResponse)
@limiter.limit("15/minute")
async def register_garage(request: Request, body: GarageRegister, db: DbSession):
    try:
        token, user_payload, user_id = await auth_service.register_garage(db, body)
        refresh = create_refresh_token(user_id)
        response = JSONResponse(content=TokenResponse(token=token, user=user_payload).model_dump())
        set_refresh_cookie(response, refresh)
        return response
    except AuthError as e:
        raise _auth_exc(e) from e


@router.post("/logout", response_model=MessageResponse)
async def logout():
    response = JSONResponse(content=MessageResponse(message="ok").model_dump())
    clear_refresh_cookie(response)
    return response


@router.get("/oauth/state")
@limiter.limit("30/minute")
async def oauth_state(request: Request):
    """Generate a new OAuth state value and store it in Redis with 5-minute expiry."""
    r = await get_redis()
    state = secrets.token_urlsafe(32)
    await r.setex(f"oauth_state:{state}", 300, "1")
    logger.debug("Generated OAuth state: %s", state[:8] + "...")
    return {"state": state}


@router.post("/oauth/google", response_model=TokenResponse)
@limiter.limit("20/minute")
async def oauth_google(request: Request, body: GoogleOAuthRequest, db: DbSession):
    # Validate CSRF state parameter against Redis store (single-use, 5-min expiry)
    if not body.state:
        logger.warning("OAuth state parameter missing in request")
        raise HTTPException(status_code=400, detail="Missing OAuth state parameter")

    r = await get_redis()
    stored = await r.get(f"oauth_state:{body.state}")
    if not stored:
        logger.warning("OAuth state not found or expired: %s", body.state[:8] + "..." if len(body.state) > 8 else body.state)
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state parameter")

    # Single-use: delete the state after successful verification
    await r.delete(f"oauth_state:{body.state}")

    settings = get_settings()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": body.id_token},
                timeout=15.0,
            )
            if r.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid Google token")
            data = r.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Google token verification timed out")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Cannot reach Google authentication service")
    if settings.google_oauth_client_id and data.get("aud") != settings.google_oauth_client_id:
        raise HTTPException(status_code=401, detail="Token audience mismatch")
    email = data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not present in token")
    from sqlalchemy import select

    res = await db.execute(select(User).where(User.email == email.lower()))
    user = res.scalar_one_or_none()
    desired_role = body.role or "customer"
    if not user:
        user = User(
            email=email.lower(),
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role=desired_role,
        )
        db.add(user)
        await db.flush()
        # If mechanic/garage, create minimal profile so dashboards don't break.
        if desired_role == "mechanic":
            full_name = (data.get("name") or email.split("@")[0]).strip() or "Mechanic"
            mech = Mechanic(
                user_id=user.id,
                full_name=full_name,
                lat=settings.default_map_lat,
                lon=settings.default_map_lon,
                verified=settings.dev_auto_verify_providers,
                available=True,
            )
            db.add(mech)
            await db.flush()
        elif desired_role == "garage":
            local = email.split("@")[0].replace(".", " ").title()
            garage_name = (data.get("hd") or local or "Garage").strip() or "Garage"
            owner_name = local or "Owner"
            g = Garage(
                user_id=user.id,
                garage_name=garage_name,
                owner_name=owner_name,
                lat=settings.default_map_lat,
                lon=settings.default_map_lon,
                verified=settings.dev_auto_verify_providers,
            )
            db.add(g)
            await db.flush()
    else:
        # If user exists, don't silently change role.
        if body.role and user.role != body.role:
            raise HTTPException(status_code=409, detail="Account already exists with a different role")
    token = create_access_token(str(user.id), {"role": user.role})
    refresh = create_refresh_token(str(user.id))
    payload = await user_to_frontend_dict(db, user)
    response = JSONResponse(content=TokenResponse(token=token, user=payload).model_dump())
    set_refresh_cookie(response, refresh)
    return response


@router.post("/forgot-password/request", response_model=MessageResponse)
@limiter.limit("3/minute")
async def forgot_password_request(request: Request, body: ForgotPasswordRequest, db: DbSession):
    from sqlalchemy import select
    email = body.email.lower()
    
    # Check per-email rate limit (stored in Redis)
    r = await get_redis()
    request_key = f"reset_rate:{email}"
    request_count = await r.get(request_key)
    if request_count and int(request_count) >= 3:
        raise HTTPException(
            status_code=429,
            detail="Too many password reset requests for this email. Please try again later.",
        )
    # Increment and set 1-hour TTL
    await r.incr(request_key)
    await r.expire(request_key, 3600)

    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if not user:
        # Don't reveal if user exists or not, just return success
        return MessageResponse(message="If an account with that email exists, a password reset code has been generated.")

    # Generate alphanumeric reset code (8+ chars, ~238x more combinations than 6-digit)
    code = secrets.token_urlsafe(6)  # 8 chars, mixed case + digits
    
    # Save to Redis with 10 min (600s) expiry
    await r.setex(f"reset:{email}", 600, code)
    
    # Track failed attempts separately
    await r.delete(f"reset_attempts:{email}")
    
    # LOG the code instead of sending an email for local testing
    logger.warning("=====================================================")
    logger.warning(f"PASSWORD RESET REQUESTED FOR {user.email}")
    logger.warning(f"YOUR RESET CODE IS: {code}")
    logger.warning("=====================================================")
    
    return MessageResponse(message="If an account with that email exists, a password reset code has been generated.")


@router.post("/forgot-password/reset", response_model=MessageResponse)
@limiter.limit("3/minute")
async def forgot_password_reset(request: Request, body: PasswordResetRequest, db: DbSession):
    from sqlalchemy import select
    email = body.email.lower()
    
    r = await get_redis()
    
    # Check for account lockout due to too many failed attempts
    attempts_key = f"reset_attempts:{email}"
    attempts = await r.get(attempts_key)
    if attempts and int(attempts) >= 5:
        raise HTTPException(
            status_code=429,
            detail="Too many failed reset attempts. Your account is temporarily locked. Try again later.",
        )
    
    stored_code = await r.get(f"reset:{email}")
    
    if not stored_code or stored_code != body.code:
        # Track the failed attempt
        if stored_code is not None:
            await r.incr(attempts_key)
            await r.expire(attempts_key, 3600)
            remaining = 4 - (int(attempts or 0))
            if remaining > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid reset code. {remaining} attempt(s) remaining before temporary lockout.",
                )
            else:
                # Delete the reset code to prevent further attempts
                await r.delete(f"reset:{email}")
                raise HTTPException(
                    status_code=429,
                    detail="Too many failed reset attempts. Account temporarily locked for 1 hour.",
                )
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")
        
    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Update password
    user.password_hash = hash_password(body.newPassword)
    await db.flush()
    
    # Clean up all reset-related keys
    await r.delete(f"reset:{email}")
    await r.delete(attempts_key)
    await r.delete(f"reset_rate:{email}")
    
    return MessageResponse(message="Password has been successfully updated.")
