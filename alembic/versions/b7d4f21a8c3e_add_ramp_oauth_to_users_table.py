"""add ramp_oauth to users table

Revision ID: b7d4f21a8c3e
Revises: 05cdf20e3f2f
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d4f21a8c3e'
down_revision: Union[str, Sequence[str], None] = '05cdf20e3f2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('ramp_oauth', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'ramp_oauth')
