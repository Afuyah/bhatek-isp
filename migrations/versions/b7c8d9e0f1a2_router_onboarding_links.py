"""Router onboarding links

Single-use, expiring capability URLs that the MikroTik fetches and imports,
so the onboarding script is never displayed in the UI.

Revision ID: b7c8d9e0f1a2
Revises: a1f2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa

revision = 'b7c8d9e0f1a2'
down_revision = 'a1f2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('routers', sa.Column('onboarding_token', sa.String(length=64), nullable=True))
    op.add_column('routers', sa.Column('onboarding_token_expires_at', sa.DateTime(), nullable=True))
    op.add_column('routers', sa.Column('onboarding_used_at', sa.DateTime(), nullable=True))
    op.create_index('ix_routers_onboarding_token', 'routers', ['onboarding_token'], unique=True)


def downgrade():
    op.drop_index('ix_routers_onboarding_token', table_name='routers')
    op.drop_column('routers', 'onboarding_used_at')
    op.drop_column('routers', 'onboarding_token_expires_at')
    op.drop_column('routers', 'onboarding_token')
