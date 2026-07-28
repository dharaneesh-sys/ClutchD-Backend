"""add marketplace_product_fitments table for vehicle compatibility

Revision ID: c2d3e4f5a6b7
Revises: b0c1d2e3f4a5
Create Date: 2026-07-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "marketplace_product_fitments" not in tables:
        op.create_table(
            "marketplace_product_fitments",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("vehicle_make", sa.String(length=100), nullable=True),
            sa.Column("vehicle_model", sa.String(length=100), nullable=True),
            sa.Column("year_start", sa.Integer(), nullable=True),
            sa.Column("year_end", sa.Integer(), nullable=True),
            sa.Column("fitment_type", sa.String(length=32), nullable=False, server_default="direct_fit"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="demo"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"],
                ["marketplace_products.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_marketplace_product_fitments_product_id"),
            "marketplace_product_fitments",
            ["product_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_marketplace_product_fitments_vehicle_make"),
            "marketplace_product_fitments",
            ["vehicle_make"],
            unique=False,
        )
        op.create_index(
            op.f("ix_marketplace_product_fitments_vehicle_model"),
            "marketplace_product_fitments",
            ["vehicle_model"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_marketplace_product_fitments_vehicle_model"), table_name="marketplace_product_fitments")
    op.drop_index(op.f("ix_marketplace_product_fitments_vehicle_make"), table_name="marketplace_product_fitments")
    op.drop_index(op.f("ix_marketplace_product_fitments_product_id"), table_name="marketplace_product_fitments")
    op.drop_table("marketplace_product_fitments")
