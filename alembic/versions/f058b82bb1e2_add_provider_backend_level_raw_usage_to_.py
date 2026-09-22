"""add provider, backend, level, raw_usage to gemini_api_calls

Revision ID: f058b82bb1e2
Revises: bf3a48a208a6
Create Date: 2026-06-05 23:54:43.205448

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f058b82bb1e2'
down_revision: Union[str, Sequence[str], None] = 'bf3a48a208a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add raw-usage / cost-analytics columns to gemini_api_calls.

    provider/backend/raw_usage are nullable (legacy rows predate them);
    level defaults to 1 (top-level / 1st-level sub-agent).
    """
    op.add_column('gemini_api_calls', sa.Column('provider', sa.String(length=20), nullable=True))
    op.add_column('gemini_api_calls', sa.Column('backend', sa.String(length=20), nullable=True))
    op.add_column('gemini_api_calls', sa.Column('level', sa.Integer(), server_default='1', nullable=False))
    op.add_column('gemini_api_calls', sa.Column('raw_usage', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove the raw-usage / cost-analytics columns from gemini_api_calls."""
    op.drop_column('gemini_api_calls', 'raw_usage')
    op.drop_column('gemini_api_calls', 'level')
    op.drop_column('gemini_api_calls', 'backend')
    op.drop_column('gemini_api_calls', 'provider')
