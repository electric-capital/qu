"""create inference_api_keys table

Revision ID: b2c7e4f1a9d3
Revises: e4b1a7c92f05
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c7e4f1a9d3'
down_revision: Union[str, Sequence[str], None] = 'e4b1a7c92f05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the inference_api_keys table."""
    op.create_table(
        'inference_api_keys',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('token_hint', sa.String(length=12), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_inference_api_keys_user_id', 'inference_api_keys',
        ['user_id'], unique=False,
    )
    op.create_index(
        'ix_inference_api_keys_token_hash', 'inference_api_keys',
        ['token_hash'], unique=True,
    )


def downgrade() -> None:
    """Drop the inference_api_keys table."""
    op.drop_index('ix_inference_api_keys_token_hash', table_name='inference_api_keys')
    op.drop_index('ix_inference_api_keys_user_id', table_name='inference_api_keys')
    op.drop_table('inference_api_keys')
