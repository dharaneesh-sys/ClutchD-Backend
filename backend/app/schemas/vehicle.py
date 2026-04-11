import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class VehicleCreate(BaseModel):
    make: str = Field(..., max_length=50)
    model: str = Field(..., max_length=50)
    year: int | None = Field(None, ge=1900, le=2100)
    license_plate: str | None = Field(None, max_length=20)
    color: str | None = Field(None, max_length=30)


class VehicleUpdate(BaseModel):
    make: str | None = Field(None, max_length=50)
    model: str | None = Field(None, max_length=50)
    year: int | None = Field(None, ge=1900, le=2100)
    license_plate: str | None = Field(None, max_length=20)
    color: str | None = Field(None, max_length=30)


class VehicleResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    make: str
    model: str
    year: int | None
    license_plate: str | None
    color: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
