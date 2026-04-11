from uuid import UUID

from pydantic import BaseModel, Field


class ServiceRequestCreate(BaseModel):
    issueTag: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=10, max_length=2000)
    requestType: str = Field(default="auto", pattern="^(mechanic|garage|auto)$")
    priceEstimate: dict | None = None
    customerLat: float | None = Field(None, ge=-90, le=90)
    customerLng: float | None = Field(None, ge=-180, le=180)
    mediaUrl: str | None = Field(None, max_length=1024)
    vehicleId: UUID | None = None


class ServiceRequestStatusUpdate(BaseModel):
    status: str = Field(
        pattern="^(searching|assigned|en_route|in_progress|completed|cancelled)$"
    )
    mechanicId: str | None = Field(None, max_length=36)


class PaymentCompleteBody(BaseModel):
    method: str = Field(max_length=32)
    amount: int = Field(ge=0, le=50000000)
    status: str = Field(max_length=32)
    transactionId: str | None = Field(None, max_length=128)


class JobAssignRequest(BaseModel):
    job_id: UUID
    assign_type: str = Field(default="mechanic", pattern="^(mechanic|garage)$")
    assign_id: UUID
