"""initialize vehicles and notifications

Revision ID: a1b2c3d4e5f6
Revises: None
Create Date: 2026-04-11 12:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use inspector to check for existence
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # Create vehicles table if it doesn't exist
    if "vehicles" not in tables:
        op.create_table(
            "vehicles",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("make", sa.String(length=50), nullable=False),
            sa.Column("model", sa.String(length=50), nullable=False),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("license_plate", sa.String(length=20), nullable=True),
            sa.Column("color", sa.String(length=30), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_vehicles_user_id"), "vehicles", ["user_id"], unique=False
        )

    # Create notifications table if it doesn't exist
    if "notifications" not in tables:
        op.create_table(
            "notifications",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(length=128), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=True),
            sa.Column("read", sa.Boolean(), nullable=True),
            sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_notifications_read"), "notifications", ["read"], unique=False
        )
        op.create_index(
            op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False
        )

    # Add vehicle_id to jobs if it doesn't exist
    columns = [c["name"] for c in inspector.get_columns("jobs")]
    if "vehicle_id" not in columns:
        op.add_column(
            "jobs",
            sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_jobs_vehicle_id",
            "jobs",
            "vehicles",
            ["vehicle_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_vehicle_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "vehicle_id")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_read"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_vehicles_user_id"), table_name="vehicles")
    op.drop_table("vehicles")
