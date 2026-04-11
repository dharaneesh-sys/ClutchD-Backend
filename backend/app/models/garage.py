from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Garage(Base):
    __tablename__ = "garages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    garage_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="")
    services: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    mechanic_count: Mapped[int] = mapped_column(Integer, default=0)
    operating_hours: Mapped[str] = mapped_column(String(128), default="")
    location_address: Mapped[str] = mapped_column(String(512), default="")
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    rating: Mapped[float] = mapped_column(Float, default=4.5)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="garage_profile")
    garage_mechanics: Mapped[list["GarageMechanic"]] = relationship("GarageMechanic", back_populates="garage")


class GarageMechanic(Base):
    __tablename__ = "garage_mechanics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    garage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("garages.id", ondelete="CASCADE"))
    mechanic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mechanics.id", ondelete="CASCADE"))

    garage: Mapped[Garage] = relationship("Garage", back_populates="garage_mechanics")
    mechanic: Mapped["Mechanic"] = relationship("Mechanic")
