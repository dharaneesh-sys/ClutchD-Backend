from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.deps import DbSession, require_roles
from app.core.limiter import limiter
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.provider_offer import AcceptDeclineResponse
from app.services.offer_service import accept_offer, decline_offer

router = APIRouter(prefix="/offers", tags=["offers"])


@router.post("/{offer_id}/accept")
@limiter.limit("10/minute")
async def accept_offer_endpoint(
    request: Request,
    offer_id: UUID,
    db: DbSession,
    user: User = Depends(require_roles(UserRole.mechanic, UserRole.garage)),
):
    result = await accept_offer(db, user, offer_id)
    return AcceptDeclineResponse(**result)


@router.post("/{offer_id}/decline")
@limiter.limit("10/minute")
async def decline_offer_endpoint(
    request: Request,
    offer_id: UUID,
    db: DbSession,
    user: User = Depends(require_roles(UserRole.mechanic, UserRole.garage)),
):
    result = await decline_offer(db, user, offer_id)
    return AcceptDeclineResponse(**result)
