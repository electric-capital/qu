"""add cached_tokens column to gemini_api_calls table

Revision ID: a7eb1d002df0
Revises: 7d31c3fd5c83
Create Date: 2026-02-26 00:06:14.138295

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7eb1d002df0'
down_revision: Union[str, Sequence[str], None] = '7d31c3fd5c83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add cached_tokens column to gemini_api_calls table."""
    op.add_column('gemini_api_calls', sa.Column('cached_tokens', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    """Remove cached_tokens column from gemini_api_calls table."""
    op.drop_column('gemini_api_calls', 'cached_tokens')
