import logging
import secrets
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.new_models import ClutchDCard, ClutchDOffer
from app.models.user import User
from app.schemas.card import ClutchDCardResponse, ClutchDOfferResponse, ClutchDOfferListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/card", tags=["card"])

TIER_THRESHOLDS = [
    ("platinum", 100000, 50),
    ("gold", 50000, 30),
    ("silver", 10000, 15),
    ("bronze", 0, 5),
]


def _generate_card_number() -> str:
    """Generate a unique card number like CDC-<8-char hex>."""
    return "CDC-" + secrets.token_hex(4).upper()


def _resolve_tier(lifetime_points: int) -> str:
    for tier, threshold, _ in TIER_THRESHOLDS:
        if lifetime_points >= threshold:
            return tier
    return "bronze"


def _resolve_discount(tier: str) -> int:
    for t, _, discount in TIER_THRESHOLDS:
        if t == tier:
            return discount
    return 5


async def _get_or_create_card(db: DbSession, user: User) -> ClutchDCard:
    """Return existing card or create one for the user."""
    r = await db.execute(select(ClutchDCard).where(ClutchDCard.user_id == user.id))
    card = r.scalar_one_or_none()
    if card is None:
        card_number = _generate_card_number()
        while True:
            existing = await db.execute(select(ClutchDCard).where(ClutchDCard.card_number == card_number))
            if existing.scalar_one_or_none() is None:
                break
            card_number = _generate_card_number()

        card = ClutchDCard(
            user_id=user.id,
            card_number=card_number,
            membership_tier="bronze",
            reward_points=0,
            lifetime_points=0,
            total_orders=0,
            total_spent=0,
        )
        db.add(card)
        await db.flush()
    return card


@router.get("", response_model=ClutchDCardResponse)
async def card_get(db: DbSession, user: CurrentUser):
    """Get or create the ClutchD Card for the current user."""
    card = await _get_or_create_card(db, user)
    # Recalculate tier based on lifetime points
    card.membership_tier = _resolve_tier(card.lifetime_points)
    await db.flush()

    return ClutchDCardResponse(
        card_number=card.card_number,
        membership_tier=card.membership_tier,
        reward_points=card.reward_points,
        lifetime_points=card.lifetime_points,
        total_orders=card.total_orders,
        total_spent=card.total_spent,
        created_at=card.created_at.isoformat() if card.created_at else None,
        updated_at=card.updated_at.isoformat() if card.updated_at else None,
    )


@router.get("/offers", response_model=ClutchDOfferListResponse)
async def card_offers(db: DbSession, user: CurrentUser):
    """List available offers for the user based on their membership tier."""
    card = await _get_or_create_card(db, user)
    card.membership_tier = _resolve_tier(card.lifetime_points)

    r = await db.execute(
        select(ClutchDOffer)
        .where(ClutchDOffer.is_active.is_(True))
        .order_by(ClutchDOffer.created_at.desc())
    )
    all_offers = r.scalars().all()

    # Filter by minimum tier requirement
    tier_order = {"bronze": 0, "silver": 1, "gold": 2, "platinum": 3}
    user_tier_idx = tier_order.get(card.membership_tier, 0)

    eligible = []
    for offer in all_offers:
        offer_tier_idx = tier_order.get(offer.min_tier, 0)
        if user_tier_idx >= offer_tier_idx:
            eligible.append(
                ClutchDOfferResponse(
                    id=str(offer.id),
                    title=offer.title,
                    description=offer.description,
                    min_tier=offer.min_tier,
                    discount_percent=offer.discount_percent,
                    discount_cap=offer.discount_cap,
                    is_active=offer.is_active,
                    valid_from=offer.valid_from.isoformat() if offer.valid_from else None,
                    valid_until=offer.valid_until.isoformat() if offer.valid_until else None,
                )
            )

    return ClutchDOfferListResponse(offers=eligible)
