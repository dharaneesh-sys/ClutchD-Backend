from fastapi import APIRouter, HTTPException, Response, Request, Cookie
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.user_payload import user_to_frontend_dict
from sqlalchemy import select
from app.models.user import User
from uuid import UUID

router = APIRouter(prefix="/auth", tags=["auth"])


class RefreshResponse(BaseModel):
    token: str


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(
    request: Request,
    db: DbSession,
    clutchd_refresh: str | None = Cookie(None),
):
    """Issue a new access token from a valid refresh token cookie."""
    token = clutchd_refresh
    if not token:
        # Fallback: check Authorization header for refresh token
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]

    if not token:
        raise HTTPException(status_code=401, detail="Refresh token required")

    payload = decode_token(token, expected_type="refresh")
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    try:
        uid = UUID(payload["sub"])
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    result = await db.execute(select(User).where(User.id == uid, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Issue new access token
    new_access = create_access_token(str(user.id), {"role": user.role})
    return RefreshResponse(token=new_access)


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Helper to set the refresh token as an httpOnly cookie."""
    settings = get_settings()
    max_age = settings.refresh_token_expire_days * 86400
    is_secure = not settings.debug
    response.set_cookie(
        key="clutchd_refresh",
        value=refresh_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=max_age,
        path="/api/auth",
    )


def clear_refresh_cookie(response: Response) -> None:
    """Helper to clear the refresh token cookie on logout."""
    response.delete_cookie(
        key="clutchd_refresh",
        path="/api/auth",
    )
