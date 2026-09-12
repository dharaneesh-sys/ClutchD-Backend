"""add seller_user_id to marketplace_products for seller ownership

Revision ID: f3a4b5c6d7e8
Revises: e4f5a6b7c8d9
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c["name"] for c in inspector.get_columns("marketplace_products")]
    if "seller_user_id" not in cols:
        op.add_column(
            "marketplace_products",
            sa.Column("seller_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_marketplace_products_seller_user_id",
            "marketplace_products",
            "users",
            ["seller_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            op.f("ix_marketplace_products_seller_user_id"),
            "marketplace_products",
            ["seller_user_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_marketplace_products_seller_user_id"), table_name="marketplace_products")
    op.drop_constraint("fk_marketplace_products_seller_user_id", "marketplace_products", type_="foreignkey")
    op.drop_column("marketplace_products", "seller_user_id")
