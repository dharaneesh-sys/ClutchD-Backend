"""add admin penalty and dispute resolution columns

Revision ID: 9a8b7c6d5e4f
Revises: 988a92da0657
Create Date: 2026-05-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a8b7c6d5e4f'
down_revision: Union[str, None] = '988a92da0657'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mechanics ADD COLUMN IF NOT EXISTS penalized BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE mechanics ADD COLUMN IF NOT EXISTS penalty_amount INTEGER")
    op.execute("ALTER TABLE garages ADD COLUMN IF NOT EXISTS penalized BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE garages ADD COLUMN IF NOT EXISTS penalty_amount INTEGER")
    op.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS resolution TEXT")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    def column_exists(table_name, column_name):
        if table_name not in inspector.get_table_names():
            return False
        columns = [c['name'] for c in inspector.get_columns(table_name)]
        return column_name in columns

    if column_exists('disputes', 'resolution'):
        op.drop_column('disputes', 'resolution')
    if column_exists('garages', 'penalty_amount'):
        op.drop_column('garages', 'penalty_amount')
    if column_exists('garages', 'penalized'):
        op.drop_column('garages', 'penalized')
    if column_exists('mechanics', 'penalty_amount'):
        op.drop_column('mechanics', 'penalty_amount')
    if column_exists('mechanics', 'penalized'):
        op.drop_column('mechanics', 'penalized')
