from pydantic import BaseModel


class ClutchDCardResponse(BaseModel):
    card_number: str
    membership_tier: str
    reward_points: int
    lifetime_points: int
    total_orders: int
    total_spent: float
    created_at: str | None = None
    updated_at: str | None = None


class ClutchDOfferResponse(BaseModel):
    id: str
    title: str
    description: str
    min_tier: str
    discount_percent: int
    discount_cap: int
    is_active: bool
    valid_from: str | None = None
    valid_until: str | None = None


class ClutchDOfferListResponse(BaseModel):
    offers: list[ClutchDOfferResponse]
