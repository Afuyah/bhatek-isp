"""Organization subdomains

Each tenant is addressed as <subdomain>.<APP_DOMAIN> instead of a UUID in the
path.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from alembic import op
import sqlalchemy as sa

revision = 'c8d9e0f1a2b3'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('subdomain', sa.String(length=63), nullable=True))
    op.create_index('ix_organizations_subdomain', 'organizations', ['subdomain'], unique=True)


def downgrade():
    op.drop_index('ix_organizations_subdomain', table_name='organizations')
    op.drop_column('organizations', 'subdomain')
