from uuid import UUID

from pydantic import BaseModel, Field


class PhotoUpdateRequest(BaseModel):
    profile_photo_url: str = Field(max_length=500)


class ProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=32)
    address: str | None = Field(None, max_length=512)


class CustomerProfileResponse(BaseModel):
    id: str
    full_name: str | None = None
    phone: str
    address: str
    profile_photo_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
