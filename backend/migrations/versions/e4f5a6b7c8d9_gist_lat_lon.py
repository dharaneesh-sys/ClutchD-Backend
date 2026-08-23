"""add idempotent GIST expression indexes on mechanics/garages lat+lon

Revision ID: e4f5a6b7c8d9
Revises: d2e3f4a5b6c7
Create Date: 2026-08-23 00:00:00.000000

Expression-only indexes over ST_SetSRID(ST_MakePoint(lon, lat), 4326) so
PostGIS radius scans stop filtering on raw lat/lon columns. Column types
are untouched (Float stays Float). Skips gracefully when the PostGIS
extension is absent (e.g. plain PG or SQLite test runs).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POINT_EXPR = "ST_SetSRID(ST_MakePoint(lon, lat), 4326)"
_INDEXES: tuple[tuple[str, str], ...] = (
    ("idx_mechanics_gist", "mechanics"),
    ("idx_garages_gist", "garages"),
)


def _ddl(index_name: str, table: str) -> str:
    return (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
        f"ON {table} USING gist (({_POINT_EXPR}))"
    )


def _postgis_present(bind) -> bool:
    try:
        row = bind.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
        ).fetchone()
    except Exception:
        return False
    return row is not None


def upgrade() -> None:
    # CONCURRENTLY cannot run inside a transaction block; autocommit_block
    # emits COMMIT/START TRANSACTION pairs in offline (--sql) rendering and
    # escapes the migration transaction online.
    with op.get_context().autocommit_block():
        if op.get_context().as_sql:
            # No live connection in offline mode — IF NOT EXISTS keeps
            # replays idempotent; PostGIS-absent DBs are guarded online only.
            for index_name, table in _INDEXES:
                op.execute(_ddl(index_name, table))
            return
        if not _postgis_present(op.get_bind()):
            print("postgis extension absent - skipping GIST expression indexes")
            return
        for index_name, table in _INDEXES:
            op.execute(_ddl(index_name, table))


def downgrade() -> None:
    for index_name, _table in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
