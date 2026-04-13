"""add_dynamic_pricing_columns

Revision ID: 988a92da0657
Revises: a1b2c3d4e5f6
Create Date: 2026-04-13 08:07:34.065814

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '988a92da0657'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### custom commands ###
    op.execute("ALTER TABLE mechanics ADD COLUMN IF NOT EXISTS upi_id VARCHAR(128)")
    op.execute("ALTER TABLE garages ADD COLUMN IF NOT EXISTS upi_id VARCHAR(128)")
    
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_amount FLOAT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS convenience_fee FLOAT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cancellation_fee FLOAT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS distance_km FLOAT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS distance_fee FLOAT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS gst_amount FLOAT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS total_amount FLOAT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS provider_upi_id VARCHAR(128)")
    # ### end custom commands ###


def downgrade() -> None:
    # ### custom commands ###
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    def column_exists(table_name, column_name):
        if table_name not in inspector.get_table_names():
            return False
        columns = [c['name'] for c in inspector.get_columns(table_name)]
        return column_name in columns

    if column_exists('jobs', 'provider_upi_id'):
        op.drop_column('jobs', 'provider_upi_id')
    if column_exists('jobs', 'total_amount'):
        op.drop_column('jobs', 'total_amount')
    if column_exists('jobs', 'gst_amount'):
        op.drop_column('jobs', 'gst_amount')
    if column_exists('jobs', 'distance_fee'):
        op.drop_column('jobs', 'distance_fee')
    if column_exists('jobs', 'distance_km'):
        op.drop_column('jobs', 'distance_km')
    if column_exists('jobs', 'cancellation_fee'):
        op.drop_column('jobs', 'cancellation_fee')
    if column_exists('jobs', 'convenience_fee'):
        op.drop_column('jobs', 'convenience_fee')
    if column_exists('jobs', 'service_amount'):
        op.drop_column('jobs', 'service_amount')
    if column_exists('garages', 'upi_id'):
        op.drop_column('garages', 'upi_id')
    if column_exists('mechanics', 'upi_id'):
        op.drop_column('mechanics', 'upi_id')
    # ### end custom commands ###
