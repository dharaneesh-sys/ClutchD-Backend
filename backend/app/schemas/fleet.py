from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class FleetRegisterBody(BaseModel):
    companyName: str = Field(min_length=2, max_length=255)
    fleetType: str = Field(min_length=2, max_length=64)
    fleetSize: str = Field(min_length=1, max_length=32)
    contactName: str = Field(min_length=2, max_length=255)
    contactEmail: EmailStr
    contactPhone: str = Field(min_length=7, max_length=20)
    businessAddress: str = Field(min_length=2, max_length=512)
    gstin: str | None = Field(None, max_length=20)


class FleetResponse(BaseModel):
    id: UUID
    companyName: str
    fleetType: str
    fleetSize: str
    contactName: str
    contactEmail: str
    contactPhone: str
    businessAddress: str
    gstin: str | None = None
    tier: str
    discountRate: int
    priorityDispatch: bool
    totalJobsCompleted: int
    totalSpent: float
    registeredAt: datetime | None = None

    class Config:
        from_attributes = True


class FleetListResponse(BaseModel):
    fleets: list[FleetResponse]
    total: int
