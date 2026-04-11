from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PaymentCreateRequest(BaseModel):
    job_id: UUID
    provider: Literal["stripe", "razorpay"]
    amount: int = Field(description="Amount in smallest currency unit (paise / cents)")
    currency: str = "inr"


class PaymentVerifyRequest(BaseModel):
    provider: str = Field(pattern="^(stripe|razorpay)$")
    job_id: UUID
    payload: dict
