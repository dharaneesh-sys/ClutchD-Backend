from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class DeviceTokenRegisterBody(BaseModel):
    token: str
    platform: Literal["android", "ios"]


class DeviceTokenResponse(BaseModel):
    id: UUID
    platform: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class DeviceTokenListResponse(BaseModel):
    tokens: list[DeviceTokenResponse]
    total: int
