from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationResponse(BaseModel):
    id: UUID
    title: str
    body: str
    type: str
    read: bool
    job_id: UUID | None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationUpdate(BaseModel):
    read: bool = True
