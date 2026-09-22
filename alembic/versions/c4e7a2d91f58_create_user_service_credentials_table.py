"""create user_service_credentials table

Revision ID: c4e7a2d91f58
Revises: b3e7f2a91c04
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e7a2d91f58'
down_revision: Union[str, Sequence[str], None] = 'b3e7f2a91c04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the user_service_credentials table.

    Per-user credentials for plugin-contributed services: one row per
    (user, service). Core integrations keep their dedicated users columns;
    plugins cannot add columns, so their per-user connections live here.
    """
    op.create_table(
        'user_service_credentials',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('service', sa.String(length=64), nullable=False),
        sa.Column('secret', sa.Text(), nullable=True),
        sa.Column('oauth_blob', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_user_service_credentials_user_id', 'user_service_credentials',
        ['user_id'], unique=False,
    )
    op.create_index(
        'ix_user_service_credentials_user_id_service',
        'user_service_credentials',
        ['user_id', 'service'], unique=True,
    )


def downgrade() -> None:
    """Drop the user_service_credentials table."""
    op.drop_index(
        'ix_user_service_credentials_user_id_service',
        table_name='user_service_credentials',
    )
    op.drop_index(
        'ix_user_service_credentials_user_id',
        table_name='user_service_credentials',
    )
    op.drop_table('user_service_credentials')
