from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PaymentCreateRequest(BaseModel):
    job_id: UUID
    provider: Literal["stripe", "razorpay"] = "razorpay"
    amount: int = Field(ge=100, le=50000000, description="Amount in paise (₹1 - ₹5,00,000)")
    currency: str = Field(default="inr", max_length=3, pattern="^[a-zA-Z]{3}$")


class PaymentVerifyRequest(BaseModel):
    provider: str = Field(pattern="^(stripe|razorpay)$")
    job_id: UUID
    payload: dict


class QRCodeCreateRequest(BaseModel):
    job_id: UUID
    amount: int = Field(ge=100, le=50000000)


class QRCodeResponse(BaseModel):
    qr_id: str
    image_url: str
    amount: int
    expires_at: str | None = None


class QRCodeStatusResponse(BaseModel):
    status: str  # "active", "closed", "expired"
    paid: bool


class CashPaymentRequest(BaseModel):
    job_id: UUID
    amount: int = Field(ge=100, le=50000000)


class PaymentHistoryItem(BaseModel):
    id: str
    job_id: str
    amount: int
    currency: str
    provider: str
    status: str
    method: str | None = None
    created_at: str | None = None


class PaymentHistoryResponse(BaseModel):
    payments: list[PaymentHistoryItem]
    total: int
