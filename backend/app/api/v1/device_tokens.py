from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.device_token import DeviceToken
from app.schemas.device_token import DeviceTokenListResponse, DeviceTokenRegisterBody, DeviceTokenResponse

router = APIRouter(prefix="/providers/device-tokens", tags=["providers"])


@router.post("", response_model=DeviceTokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register_device_token(
    request: Request,
    body: DeviceTokenRegisterBody,
    db: DbSession,
    user: CurrentUser,
):
    """Register or refresh a device token for push notifications."""
    # Check if token already exists
    r = await db.execute(select(DeviceToken).where(DeviceToken.token == body.token))
    existing = r.scalar_one_or_none()

    if existing:
        if existing.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This device token is already registered to another user",
            )
        # Reactivate and update platform
        existing.is_active = True
        existing.platform = body.platform
        await db.flush()
        await db.refresh(existing)
        return existing

    # Create new device token
    token = DeviceToken(
        user_id=user.id,
        token=body.token,
        platform=body.platform,
        is_active=True,
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)
    return token


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def unregister_device_token(
    request: Request,
    token_id: UUID,
    db: DbSession,
    user: CurrentUser,
):
    """Unregister (soft-delete) a device token."""
    r = await db.execute(
        select(DeviceToken).where(DeviceToken.id == token_id, DeviceToken.user_id == user.id)
    )
    token = r.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device token not found")

    token.is_active = False
    await db.flush()
    return None


@router.get("", response_model=DeviceTokenListResponse)
async def list_device_tokens(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    """List the current user's active device tokens."""
    r = await db.execute(
        select(DeviceToken)
        .where(DeviceToken.user_id == user.id, DeviceToken.is_active == True)
        .order_by(DeviceToken.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    tokens = r.scalars().all()
    return DeviceTokenListResponse(
        tokens=[DeviceTokenResponse.model_validate(t) for t in tokens],
        total=len(tokens),
    )
