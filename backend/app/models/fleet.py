from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Fleet(Base):
    """B2B fleet account registered via POST /fleet/register."""

    __tablename__ = "fleets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        comment="Owning user, when registered by a signed-in account",
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    fleet_type: Mapped[str] = mapped_column(String(64), default="other")
    fleet_size: Mapped[str] = mapped_column(String(32), default="1-5")
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(32), default="")
    business_address: Mapped[str] = mapped_column(String(512), default="")
    gstin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tier: Mapped[str] = mapped_column(String(32), default="bronze")
    discount_rate: Mapped[int] = mapped_column(Integer, default=5, comment="Percent discount")
    priority_dispatch: Mapped[bool] = mapped_column(Boolean, default=True)
    total_jobs_completed: Mapped[int] = mapped_column(Integer, default=0)
    total_spent: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FleetBooking(Base):
    """A bulk service booking made by a fleet account (POST /fleet/bookings)."""

    __tablename__ = "fleet_bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fleet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fleets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vehicle_count: Mapped[int] = mapped_column(Integer, default=1)
    # [{vehicleId, vehicleName, serviceType}] — vehicle rows at booking time
    vehicles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
