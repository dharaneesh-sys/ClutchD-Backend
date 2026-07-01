"""add profile, referral, ticket, faq, and settings tables

Revision ID: b0c1d2e3f4a5
Revises: 9a8b7c6d5e4f
Create Date: 2026-07-01 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "9a8b7c6d5e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # ── CustomerProfile ──────────────────────────────────────────
    if "customer_profiles" not in tables:
        op.create_table(
            "customer_profiles",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("full_name", sa.String(length=255), nullable=True),
            sa.Column("phone", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("address", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("profile_photo_url", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )

    # ── ReferralCode ─────────────────────────────────────────────
    if "referral_codes" not in tables:
        op.create_table(
            "referral_codes",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("reward_balance", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_referrals", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )
        op.create_index(op.f("ix_referral_codes_code"), "referral_codes", ["code"], unique=True)

    # ── ReferralReward ───────────────────────────────────────────
    if "referral_rewards" not in tables:
        op.create_table(
            "referral_rewards",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("referrer_user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("referred_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("referred_email", sa.String(length=320), nullable=True),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["referrer_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    # ── SupportTicket ────────────────────────────────────────────
    if "support_tickets" not in tables:
        op.create_table(
            "support_tickets",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False, server_default="general"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
            sa.Column("ticket_number", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ticket_number"),
        )

    # ── FAQ ──────────────────────────────────────────────────────
    if "faqs" not in tables:
        op.create_table(
            "faqs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False, server_default="general"),
            sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )

    # ── UserSettings ─────────────────────────────────────────────
    if "user_settings" not in tables:
        op.create_table(
            "user_settings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("push_notifications", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("sms_notifications", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("email_notifications", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("theme", sa.String(length=16), nullable=False, server_default="system"),
            sa.Column("language", sa.String(length=8), nullable=False, server_default="en"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "user_settings" in tables:
        op.drop_table("user_settings")
    if "faqs" in tables:
        op.drop_table("faqs")
    if "support_tickets" in tables:
        op.drop_table("support_tickets")
    if "referral_rewards" in tables:
        op.drop_table("referral_rewards")
    if "referral_codes" in tables:
        op.drop_index(op.f("ix_referral_codes_code"), table_name="referral_codes")
        op.drop_table("referral_codes")
    if "customer_profiles" in tables:
        op.drop_table("customer_profiles")
