from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)

    issue_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    request_type: Mapped[str] = mapped_column(String(32), default="auto")
    status: Mapped[str] = mapped_column(String(32), default="searching", index=True)

    customer_lat: Mapped[float] = mapped_column(Float, nullable=False)
    customer_lon: Mapped[float] = mapped_column(Float, nullable=False)

    price_estimate: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)

    assigned_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    assigned_mechanic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mechanics.id", ondelete="SET NULL"), nullable=True
    )
    assigned_garage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("garages.id", ondelete="SET NULL"), nullable=True
    )

    media_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer: Mapped["User"] = relationship("User", back_populates="jobs", foreign_keys=[user_id])
    assigned_mechanic: Mapped["Mechanic | None"] = relationship("Mechanic", foreign_keys=[assigned_mechanic_id])
    assigned_garage: Mapped["Garage | None"] = relationship("Garage", foreign_keys=[assigned_garage_id])
    reviews: Mapped[list["Review"]] = relationship("Review", back_populates="job")
    payments: Mapped[list["Payment"]] = relationship("Payment", back_populates="job")
