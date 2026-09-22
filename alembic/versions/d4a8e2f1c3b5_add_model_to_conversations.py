"""add model to conversations

Revision ID: d4a8e2f1c3b5
Revises: cc3c9d86f70f
Create Date: 2026-03-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a8e2f1c3b5'
down_revision: Union[str, Sequence[str], None] = 'cc3c9d86f70f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add model column to conversations."""
    op.add_column('conversations', sa.Column('model', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Remove model from conversations."""
    op.drop_column('conversations', 'model')
