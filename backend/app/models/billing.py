"""Partner billing: provider ledger, garage rent invoices, incentive rules and
onboarding progress.

All money is integer paise. The ledger is an accounting record only — there
is no payout/settlement rail and no job-earning rows.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEFAULT_RENT_PAISE = 49900  # ₹499 / month (admin-editable via config)
DEFAULT_INCENTIVE_BPS = 500  # 5% of service_amount

LEDGER_KINDS = ("incentive", "rent_charge", "rent_payment", "waiver", "adjustment")
INVOICE_STATUSES = ("issued", "payment_pending", "paid", "waived", "overdue")
ONBOARDING_STEPS = ("profile", "garage_details", "services", "location", "kyc_review", "done")


class ProviderLedgerEntry(Base):
    __tablename__ = "provider_ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('incentive','rent_charge','rent_payment','waiver','adjustment')",
            name="ck_ledger_kind",
        ),
        CheckConstraint("provider_type IN ('mechanic','garage')", name="ck_ledger_provider_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_type: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    rent_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rent_invoices.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Signed: credits (incentive, rent_payment, waiver) > 0, rent_charge < 0.
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="posted")
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RentInvoice(Base):
    __tablename__ = "rent_invoices"
    __table_args__ = (
        UniqueConstraint("garage_id", "period_start", "period_end", name="uq_rent_invoice_period"),
        CheckConstraint("amount_paise > 0", name="ck_rent_amount_positive"),
        CheckConstraint(
            "status IN ('issued','payment_pending','paid','waived','overdue')", name="ck_rent_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    garage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("garages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_RENT_PAISE)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    waived_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    waived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    waiver_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    generation_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IncentiveRule(Base):
    __tablename__ = "incentive_rules"
    __table_args__ = (
        CheckConstraint("rate_bps > 0 AND rate_bps <= 10000", name="ck_incentive_rate_range"),
        CheckConstraint("scope IN ('global','garage','mechanic')", name="ck_incentive_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="global")
    garage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("garages.id", ondelete="CASCADE"), nullable=True
    )
    mechanic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mechanics.id", ondelete="CASCADE"), nullable=True
    )
    rate_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_INCENTIVE_BPS)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BillingConfig(Base):
    """Single-row admin config for rent defaults (id is always 1)."""

    __tablename__ = "billing_config"
    __table_args__ = (
        CheckConstraint("rent_amount_paise > 0", name="ck_billing_rent_positive"),
        CheckConstraint("rent_due_day BETWEEN 1 AND 28", name="ck_billing_due_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    rent_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_RENT_PAISE)
    rent_due_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProviderOnboarding(Base):
    __tablename__ = "provider_onboarding"
    __table_args__ = (
        UniqueConstraint("provider_type", "provider_id", name="uq_onboarding_provider"),
        CheckConstraint(
            "step IN ('profile','garage_details','services','location','kyc_review','done')",
            name="ck_onboarding_step",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_type: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step: Mapped[str] = mapped_column(String(16), nullable=False, default="profile")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
