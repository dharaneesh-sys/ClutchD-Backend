import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy.orm import relationship
from app.db.base import Base


class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Integer, nullable=False)  # in paise (100 = ₹1)
    currency = Column(String(3), default="inr", nullable=False)
    provider = Column(String(32), nullable=False)  # "razorpay", "stripe", "manual"
    provider_order_id = Column(String(128), nullable=True)  # Razorpay order_id
    provider_payment_id = Column(String(128), nullable=True)  # Razorpay payment_id
    status = Column(String(32), default="pending", nullable=False)  # pending, captured, failed, refunded
    method = Column(String(32), nullable=True)  # "upi", "card", "qr", "cash", "online"
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    job = relationship("Job", back_populates="payments")
