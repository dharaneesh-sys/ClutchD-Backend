"""fleet_bookings table for fleet bulk service bookings.

Revision ID: e6f7a8b9c4d2
Revises: d5e6f7a8b9c3
Create Date: 2026-09-14

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e6f7a8b9c4d2"
down_revision = "d5e6f7a8b9c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "fleet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fleets.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vehicle_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("vehicles", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("subtotal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="confirmed"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("fleet_bookings")
