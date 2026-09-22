"""add client_request_id to marketplace_products

Revision ID: c3d4e5f6a7b8
Revises: 988a92da0657
Create Date: 2026-09-22

Idempotency for POST /products: the client sends a stable clientRequestId
(UUID) per form submission. The server stores it with a UNIQUE constraint —
a retried/replayed submission with the same key returns the original product
(200) instead of creating a duplicate row.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "988a92da0657"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "marketplace_products",
        sa.Column("client_request_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_marketplace_products_client_request_id",
        "marketplace_products",
        ["client_request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_marketplace_products_client_request_id",
        table_name="marketplace_products",
    )
    op.drop_column("marketplace_products", "client_request_id")
