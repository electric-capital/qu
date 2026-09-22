"""add archived column to conversations table

Revision ID: 498112eb3e5d
Revises: ae661b3ecf4c
Create Date: 2026-02-23 19:24:44.182264

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '498112eb3e5d'
down_revision: Union[str, Sequence[str], None] = 'ae661b3ecf4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add archived column to conversations table."""
    op.add_column('conversations', sa.Column('archived', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    """Remove archived column from conversations table."""
    op.drop_column('conversations', 'archived')
