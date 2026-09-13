"""KYC review status columns for mechanics and garages.

Adds explicit kyc_status (pending|submitted|verified|rejected) and kyc_note
(admin review note) so reject is a real state instead of verified=False.

Revision ID: b2c3d4e5f6a1
Revises: a9b8c7d6e5f4
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a1"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None

_BACKFILL = """
UPDATE {table} SET kyc_status = CASE
    WHEN verified THEN 'verified'
    WHEN aadhaar_photo_url IS NOT NULL OR license_photo_url IS NOT NULL THEN 'submitted'
    ELSE 'pending'
END
"""


def upgrade() -> None:
    op.add_column(
        "mechanics",
        sa.Column("kyc_status", sa.String(16), server_default="pending", nullable=False),
    )
    op.add_column("mechanics", sa.Column("kyc_note", sa.String(1024), nullable=True))
    op.add_column(
        "garages",
        sa.Column("kyc_status", sa.String(16), server_default="pending", nullable=False),
    )
    op.add_column("garages", sa.Column("kyc_note", sa.String(1024), nullable=True))

    # Preserve the status the app has been deriving until now.
    op.execute(_BACKFILL.format(table="mechanics"))
    op.execute(_BACKFILL.format(table="garages"))


def downgrade() -> None:
    op.drop_column("garages", "kyc_note")
    op.drop_column("garages", "kyc_status")
    op.drop_column("mechanics", "kyc_note")
    op.drop_column("mechanics", "kyc_status")
