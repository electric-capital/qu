"""add google_sub to users table

Revision ID: b8d41f6a9c27
Revises: a7c3e91b52d8
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d41f6a9c27'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91b52d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('google_sub', sa.String(length=64), nullable=True))
    op.create_index('ix_users_google_sub', 'users', ['google_sub'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_users_google_sub', table_name='users')
    op.drop_column('users', 'google_sub')
