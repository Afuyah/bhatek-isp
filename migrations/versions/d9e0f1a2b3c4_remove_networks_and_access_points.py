"""Remove the network and access-point layers

Routers are described directly; there is no network grouping and the platform
does not manage access points. Drops the join columns first, then the tables.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
"""

from alembic import op
import sqlalchemy as sa

revision = 'd9e0f1a2b3c4'
down_revision = 'c8d9e0f1a2b3'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE routers DROP COLUMN IF EXISTS network_id CASCADE')
    op.execute('ALTER TABLE active_sessions DROP COLUMN IF EXISTS access_point_id CASCADE')
    op.execute('DROP TABLE IF EXISTS access_points CASCADE')
    op.execute('DROP TABLE IF EXISTS networks CASCADE')


def downgrade():
    # Tables are recreated by an older revision; this migration is intentionally
    # one-way because the data they held is no longer part of the product.
    pass
