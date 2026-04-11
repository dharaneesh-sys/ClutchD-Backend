from uuid import UUID

from pydantic import BaseModel, Field


class ReviewCreate(BaseModel):
    job_id: UUID
    rating: float = Field(ge=1, le=5)
    comment: str | None = None


class ReviewOut(BaseModel):
    id: UUID
    job_id: UUID
    rating: float
    comment: str | None
    target_type: str

    model_config = {"from_attributes": True}
