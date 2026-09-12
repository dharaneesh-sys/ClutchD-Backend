"""fleets table + KYC photo columns on mechanics/garages

Revision ID: a9b8c7d6e5f4
Revises: e4f5a6b7c8d9, f3a4b5c6d7e8
Create Date: 2026-09-12 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = ("e4f5a6b7c8d9", "f3a4b5c6d7e8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KYC_COLUMNS = ("aadhaar_photo_url", "license_photo_url")


def _add_kyc_columns(table: str) -> None:
    inspector = sa.inspect(op.get_bind())
    cols = [c["name"] for c in inspector.get_columns(table)]
    with op.batch_alter_table(table) as batch_op:
        for col in KYC_COLUMNS:
            if col not in cols:
                batch_op.add_column(sa.Column(col, sa.String(length=1024), nullable=True))


def _drop_kyc_columns(table: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        for col in KYC_COLUMNS:
            batch_op.drop_column(col)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()

    if "fleets" not in tables:
        op.create_table(
            "fleets",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("company_name", sa.String(length=255), nullable=False),
            sa.Column("fleet_type", sa.String(length=64), nullable=True),
            sa.Column("fleet_size", sa.String(length=32), nullable=True),
            sa.Column("contact_name", sa.String(length=255), nullable=False),
            sa.Column("contact_email", sa.String(length=255), nullable=False),
            sa.Column("contact_phone", sa.String(length=32), nullable=True),
            sa.Column("business_address", sa.String(length=512), nullable=True),
            sa.Column("gstin", sa.String(length=20), nullable=True),
            sa.Column("tier", sa.String(length=32), nullable=True),
            sa.Column("discount_rate", sa.Integer(), nullable=True),
            sa.Column("priority_dispatch", sa.Boolean(), nullable=True),
            sa.Column("total_jobs_completed", sa.Integer(), nullable=True),
            sa.Column("total_spent", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
    op.create_index(op.f("ix_fleets_user_id"), "fleets", ["user_id"], unique=False)
    op.create_index(op.f("ix_fleets_contact_email"), "fleets", ["contact_email"], unique=False)

    _add_kyc_columns("mechanics")
    _add_kyc_columns("garages")


def downgrade() -> None:
    op.drop_index(op.f("ix_fleets_contact_email"), table_name="fleets")
    op.drop_index(op.f("ix_fleets_user_id"), table_name="fleets")
    op.drop_table("fleets")
    _drop_kyc_columns("garages")
    _drop_kyc_columns("mechanics")
