"""partner billing: ledger, rent invoices, incentive rules, onboarding

Also merges the two pre-existing heads (c3d4e5f6a7b8, e6f7a8b9c4d2).

Revision ID: f7a8b9c0d1e2
Revises: c3d4e5f6a7b8, e6f7a8b9c4d2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = ("c3d4e5f6a7b8", "e6f7a8b9c4d2")
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "rent_invoices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("garage_id", UUID, sa.ForeignKey("garages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False, server_default="49900"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="issued"),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("reference", sa.String(128)),
        sa.Column("confirmed_by", UUID),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("waived_by", UUID),
        sa.Column("waived_at", sa.DateTime(timezone=True)),
        sa.Column("waiver_reason", sa.String(512)),
        sa.Column("generation_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("garage_id", "period_start", "period_end", name="uq_rent_invoice_period"),
        sa.CheckConstraint("amount_paise > 0", name="ck_rent_amount_positive"),
        sa.CheckConstraint("status IN ('issued','payment_pending','paid','waived','overdue')", name="ck_rent_status"),
    )
    op.create_index("ix_rent_invoices_garage_id", "rent_invoices", ["garage_id"])

    op.create_table(
        "provider_ledger_entries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("provider_type", sa.String(16), nullable=False),
        sa.Column("provider_id", UUID, nullable=False),
        sa.Column("job_id", UUID, sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("rent_invoice_id", UUID, sa.ForeignKey("rent_invoices.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="posted"),
        sa.Column("note", sa.String(512)),
        sa.Column("effective_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('incentive','rent_charge','rent_payment','waiver','adjustment')", name="ck_ledger_kind"),
        sa.CheckConstraint("provider_type IN ('mechanic','garage')", name="ck_ledger_provider_type"),
    )
    op.create_index("ix_provider_ledger_entries_provider_id", "provider_ledger_entries", ["provider_id"])

    op.create_table(
        "incentive_rules",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
        sa.Column("garage_id", UUID, sa.ForeignKey("garages.id", ondelete="CASCADE")),
        sa.Column("mechanic_id", UUID, sa.ForeignKey("mechanics.id", ondelete="CASCADE")),
        sa.Column("rate_bps", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("effective_from", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("rate_bps > 0 AND rate_bps <= 10000", name="ck_incentive_rate_range"),
        sa.CheckConstraint("scope IN ('global','garage','mechanic')", name="ck_incentive_scope"),
    )

    op.create_table(
        "billing_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rent_amount_paise", sa.Integer(), nullable=False, server_default="49900"),
        sa.Column("rent_due_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", UUID),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("rent_amount_paise > 0", name="ck_billing_rent_positive"),
        sa.CheckConstraint("rent_due_day BETWEEN 1 AND 28", name="ck_billing_due_day"),
    )

    op.create_table(
        "provider_onboarding",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("provider_type", sa.String(16), nullable=False),
        sa.Column("provider_id", UUID, nullable=False),
        sa.Column("step", sa.String(16), nullable=False, server_default="profile"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider_type", "provider_id", name="uq_onboarding_provider"),
        sa.CheckConstraint("step IN ('profile','garage_details','services','location','kyc_review','done')", name="ck_onboarding_step"),
    )

    # Nullable override only: existing garage rows are untouched.
    op.add_column("garages", sa.Column("rent_amount_paise", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("garages", "rent_amount_paise")
    op.drop_table("provider_onboarding")
    op.drop_table("billing_config")
    op.drop_table("incentive_rules")
    op.drop_index("ix_provider_ledger_entries_provider_id", table_name="provider_ledger_entries")
    op.drop_table("provider_ledger_entries")
    op.drop_index("ix_rent_invoices_garage_id", table_name="rent_invoices")
    op.drop_table("rent_invoices")
