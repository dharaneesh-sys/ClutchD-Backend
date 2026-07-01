from uuid import UUID

from pydantic import BaseModel, Field


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1)
    category: str = Field(default="general", max_length=64)
    priority: str = Field(default="normal", max_length=16)


class TicketResponse(BaseModel):
    id: str
    ticket_number: str
    subject: str
    message: str
    category: str
    status: str
    priority: str
    created_at: str | None = None
    updated_at: str | None = None
    resolved_at: str | None = None


class TicketListResponse(BaseModel):
    tickets: list[TicketResponse]
    total: int


class TicketCloseResponse(BaseModel):
    id: str
    ticket_number: str
    status: str
    message: str
