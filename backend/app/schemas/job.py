from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PriceEstimate(BaseModel):
    min: float = Field(alias="min")
    max: float = Field(alias="max")

    model_config = {"populate_by_name": True}


class ServiceRequestCreate(BaseModel):
    issueTag: str = Field(min_length=1)
    description: str = Field(min_length=10)
    requestType: Literal["mechanic", "garage", "auto"] = "auto"
    priceEstimate: dict[str, float] | None = None
    customerLat: float | None = None
    customerLng: float | None = None
    mediaUrl: str | None = None


class ServiceRequestStatusUpdate(BaseModel):
    status: str
    mechanicId: str | None = None


class JobAssignRequest(BaseModel):
    job_id: UUID
    assign_type: Literal["mechanic", "garage"]
    assign_id: UUID


class PaymentCompleteBody(BaseModel):
    method: str | None = None
    amount: float | None = None
    status: str | None = None
    transactionId: str | None = None


class JobCreateSpec(BaseModel):
    """Spec-style job create (alias)."""

    user_id: UUID | None = None
    assigned_type: str | None = None
    assigned_id: UUID | None = None
    price: float | None = None
