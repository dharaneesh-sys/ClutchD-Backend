from uuid import UUID

from pydantic import BaseModel, Field


class ReviewCreate(BaseModel):
    job_id: UUID
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(None, max_length=1000)
