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


class ProductCreate(BaseModel):
    """Seller product-creation payload.

    Category may be given as a UUID string, slug, or display name via
    ``category``, or directly as ``category_id``. Image is a URL
    returned by POST /api/uploads (``/static/uploads/...``) or an
    absolute http(s) URL.
    """

    name: str = Field(min_length=3, max_length=255)
    price: Decimal = Field(ge=0)
    description: str | None = None
    brand: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    category_id: UUID | None = None
    vendor_id: UUID | None = None
    # Photo of the actual part is compulsory for new listings.
    # Accepts the URL returned by POST /api/uploads (/static/uploads/...)
    # or an absolute http(s) URL.
    image: str = Field(min_length=1, max_length=500)
    availability: bool = True
    delivery_time: str | None = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Name must not be blank")
        return v.strip()

    @field_validator("image")
    @classmethod
    def validate_image_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Part photo is required")
        return v.strip()


class ProductUpdate(BaseModel):
    """Partial update payload - all fields optional."""

    name: str | None = Field(default=None, min_length=3, max_length=255)
    price: Decimal | None = Field(default=None, ge=0)
    description: str | None = None
    brand: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    category_id: UUID | None = None
    vendor_id: UUID | None = None
    image: str | None = Field(default=None, min_length=1, max_length=500)
    availability: bool | None = None
    delivery_time: str | None = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Name must not be blank")
        return v.strip() if isinstance(v, str) else v


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


# ── Fitment ──────────────────────────────────────────────────────────────

class FitmentCheckResult(BaseModel):
    compatible: bool
    fitment_type: str = "unknown"
    non_fitting_parts: list[str] = []
    source: str = "demo"


class FitmentRecordResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    product_id: UUID
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    year_start: int | None = None
    year_end: int | None = None
    fitment_type: str
    notes: str | None = None
    source: str = "demo"


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
