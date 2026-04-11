import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.api.deps import DbSession
from app.core.limiter import limiter
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
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
)
from app.services import auth_service
from app.services.auth_service import AuthError
from app.services.user_payload import user_to_frontend_dict

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_exc(e: AuthError) -> HTTPException:
    return HTTPException(status_code=e.code, detail=e.message)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("30/minute")
async def login_route(request: Request, body: LoginRequest, db: DbSession):
    try:
        token, user = await auth_service.login(db, body.email, body.password)
        return TokenResponse(token=token, user=user)
    except AuthError as e:
        raise _auth_exc(e) from e


@router.post("/signup", response_model=TokenResponse)
@limiter.limit("15/minute")
async def signup_route(request: Request, body: SignupPayload, db: DbSession):
    try:
        token, user = await auth_service.signup_from_payload(db, body)
        return TokenResponse(token=token, user=user)
    except AuthError as e:
        raise _auth_exc(e) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/register/customer", response_model=TokenResponse)
@limiter.limit("15/minute")
async def register_customer(request: Request, body: CustomerRegister, db: DbSession):
    try:
        token, user = await auth_service.register_customer(db, body)
        return TokenResponse(token=token, user=user)
    except AuthError as e:
        raise _auth_exc(e) from e


@router.post("/register/mechanic", response_model=TokenResponse)
@limiter.limit("15/minute")
async def register_mechanic(request: Request, body: MechanicRegister, db: DbSession):
    try:
        token, user = await auth_service.register_mechanic(db, body)
        return TokenResponse(token=token, user=user)
    except AuthError as e:
        raise _auth_exc(e) from e


@router.post("/register/garage", response_model=TokenResponse)
@limiter.limit("15/minute")
async def register_garage(request: Request, body: GarageRegister, db: DbSession):
    try:
        token, user = await auth_service.register_garage(db, body)
        return TokenResponse(token=token, user=user)
    except AuthError as e:
        raise _auth_exc(e) from e


@router.post("/logout", response_model=MessageResponse)
async def logout():
    return MessageResponse(message="ok")


@router.post("/oauth/google", response_model=TokenResponse)
@limiter.limit("20/minute")
async def oauth_google(request: Request, body: GoogleOAuthRequest, db: DbSession):
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": body.id_token},
            timeout=15.0,
        )
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid Google token")
        data = r.json()
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
    payload = await user_to_frontend_dict(db, user)
    return TokenResponse(token=token, user=payload)
