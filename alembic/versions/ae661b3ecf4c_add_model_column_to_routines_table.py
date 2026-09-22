"""Add model column to routines table

Revision ID: ae661b3ecf4c
Revises: c7e3f1a2b4d6
Create Date: 2026-02-20 23:27:59.527397

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ae661b3ecf4c'
down_revision: Union[str, Sequence[str], None] = 'c7e3f1a2b4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('routines', sa.Column('model', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('routines', 'model')
