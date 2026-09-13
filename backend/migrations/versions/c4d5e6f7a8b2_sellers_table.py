"""sellers table for the parts & accessories seller role

Revision ID: c4d5e6f7a8b2
Revises: b2c3d4e5f6a1
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4d5e6f7a8b2"
down_revision = "b2c3d4e5f6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sellers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("store_name", sa.String(length=255), nullable=False),
        sa.Column("owner_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("location_address", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("upi_id", sa.String(length=128), nullable=True),
        sa.Column("rating", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sellers_user_id", "sellers", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sellers_user_id", table_name="sellers")
    op.drop_table("sellers")
