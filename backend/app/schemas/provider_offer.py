"""Schemas for provider offer accept/decline."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AcceptDeclineResponse(BaseModel):
    """Response returned after a successful accept or decline."""

    id: UUID
    status: str
    job_id: UUID
    job_status: str
    message: str

    class Config:
        from_attributes = True
