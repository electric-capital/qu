"""Add airtable_token to users table

Revision ID: eea78c9364cf
Revises: 4e8960c4dacc
Create Date: 2026-02-18 18:55:22.241870

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eea78c9364cf'
down_revision: Union[str, Sequence[str], None] = '4e8960c4dacc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add airtable_token column to users table."""
    op.add_column('users', sa.Column('airtable_token', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Remove airtable_token column from users table."""
    op.drop_column('users', 'airtable_token')
