from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── Category ─────────────────────────────────────────────────────────────

class CategoryResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None = None
    image: str | None = None
    product_count: int = 0
    created_at: datetime | None = None


class CategoryListResponse(BaseModel):
    categories: list[CategoryResponse]


# ── Product ──────────────────────────────────────────────────────────────

class ProductResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    brand: str | None = None
    vendor_id: UUID | None = None
    vendor: str | None = None
    price: Decimal
    rating: Decimal = Decimal("0")
    image: str | None = None
    category_id: UUID | None = None
    category: str | None = None
    availability: bool = True
    delivery_time: str | None = None
    created_at: datetime | None = None


class ProductListResponse(BaseModel):
    products: list[ProductResponse]


# ── Offer ────────────────────────────────────────────────────────────────

class OfferValidateRequest(BaseModel):
    code: str
    purchaseAmount: Decimal = Field(default=0, ge=0)


class OfferValidateResponse(BaseModel):
    valid: bool
    code: str | None = None
    discountAmount: Decimal = Decimal("0")
    message: str | None = None


# ── Product Reviews ──────────────────────────────────────────────────────

class ProductReviewResponse(BaseModel):
    id: UUID
    productId: UUID
    userName: str | None = None
    rating: int
    text: str | None = None
    date: datetime | None = None
    verified: bool = False


class ProductReviewListResponse(BaseModel):
    reviews: list[ProductReviewResponse]


class ProductReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str | None = Field(None, max_length=2000)
    userName: str | None = Field(None, max_length=100)

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v):
        if v < 1 or v > 5:
            raise ValueError("Rating must be between 1 and 5")
        return v


# ── Cart ─────────────────────────────────────────────────────────────────

class CartItemCreate(BaseModel):
    product_id: UUID
    vendor_id: UUID | None = None
    quantity: int = Field(default=1, ge=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=0)


class CartItemResponse(BaseModel):
    id: UUID
    user_id: UUID
    product_id: UUID
    vendor_id: UUID | None = None
    quantity: int
    created_at: datetime | None = None


# ── Order ────────────────────────────────────────────────────────────────

class OrderItemData(BaseModel):
    product_id: UUID | None = None
    name: str | None = None
    quantity: int
    price: Decimal


class OrderCreate(BaseModel):
    items: list[OrderItemData]
    address: dict | None = None
    payment: dict | None = None


class OrderResponse(BaseModel):
    id: UUID
    user_id: UUID
    total: Decimal
    status: str = "pending"
    address: dict | None = None
    payment: dict | None = None
    items: list[OrderItemData] = []
    created_at: datetime | None = None


class OrderListResponse(BaseModel):
    orders: list[OrderResponse]
