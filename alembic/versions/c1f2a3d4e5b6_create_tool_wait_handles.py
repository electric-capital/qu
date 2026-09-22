"""create tool_wait_handles table

Revision ID: c1f2a3d4e5b6
Revises: e58a2bf14c01
Create Date: 2026-04-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1f2a3d4e5b6'
down_revision: Union[str, Sequence[str], None] = 'e58a2bf14c01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the tool_wait_handles table."""
    op.create_table(
        'tool_wait_handles',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=50), nullable=False),
        sa.Column('tool_id', sa.String(length=64), nullable=False),
        sa.Column(
            'status', sa.String(length=20),
            server_default='pending', nullable=False,
        ),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('response', sa.JSON(), nullable=True),
        sa.Column('correlation_kind', sa.String(length=50), nullable=True),
        sa.Column('correlation_id', sa.String(length=64), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_tool_wait_handles_user_id', 'tool_wait_handles',
        ['user_id'], unique=False,
    )
    op.create_index(
        'ix_tool_wait_handles_conversation_id', 'tool_wait_handles',
        ['conversation_id'], unique=False,
    )
    op.create_index(
        'ix_tool_wait_handles_user_id_status', 'tool_wait_handles',
        ['user_id', 'status'], unique=False,
    )
    op.create_index(
        'ix_tool_wait_handles_tool_id', 'tool_wait_handles',
        ['tool_id'], unique=False,
    )


def downgrade() -> None:
    """Drop the tool_wait_handles table."""
    op.drop_index('ix_tool_wait_handles_tool_id', table_name='tool_wait_handles')
    op.drop_index('ix_tool_wait_handles_user_id_status', table_name='tool_wait_handles')
    op.drop_index('ix_tool_wait_handles_conversation_id', table_name='tool_wait_handles')
    op.drop_index('ix_tool_wait_handles_user_id', table_name='tool_wait_handles')
    op.drop_table('tool_wait_handles')
