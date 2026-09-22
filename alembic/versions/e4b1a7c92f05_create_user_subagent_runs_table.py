"""create user_subagent_runs table

Revision ID: e4b1a7c92f05
Revises: b7d4f21a8c3e
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4b1a7c92f05'
down_revision: Union[str, Sequence[str], None] = 'b7d4f21a8c3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the user_subagent_runs table."""
    op.create_table(
        'user_subagent_runs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('caller_user_id', sa.Integer(), nullable=False),
        sa.Column('target_user_id', sa.Integer(), nullable=False),
        sa.Column('caller_conversation_id', sa.String(length=36), nullable=False),
        sa.Column('subagent_conversation_id', sa.String(length=36), nullable=False),
        sa.Column('wait_handle_id', sa.String(length=36), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('skill_ids', sa.JSON(), nullable=False),
        sa.Column(
            'status', sa.String(length=20),
            server_default='running', nullable=False,
        ),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        # No FKs: user ids mirror the llm_calls_* tables (plain integers)
        # and conversation ids mirror action_requests, so run rows survive
        # deletion of either user or conversation -- they are the durable
        # cost-attribution linkage between a caller conversation and its
        # subagent conversation's llm_calls_* rows.
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_user_subagent_runs_subagent_conversation_id',
        'user_subagent_runs', ['subagent_conversation_id'], unique=False,
    )
    op.create_index(
        'ix_user_subagent_runs_caller_conversation_id',
        'user_subagent_runs', ['caller_conversation_id'], unique=False,
    )
    op.create_index(
        'ix_user_subagent_runs_caller_user_id',
        'user_subagent_runs', ['caller_user_id'], unique=False,
    )
    op.create_index(
        'ix_user_subagent_runs_target_user_id',
        'user_subagent_runs', ['target_user_id'], unique=False,
    )


def downgrade() -> None:
    """Drop the user_subagent_runs table."""
    op.drop_index(
        'ix_user_subagent_runs_target_user_id', table_name='user_subagent_runs',
    )
    op.drop_index(
        'ix_user_subagent_runs_caller_user_id', table_name='user_subagent_runs',
    )
    op.drop_index(
        'ix_user_subagent_runs_caller_conversation_id',
        table_name='user_subagent_runs',
    )
    op.drop_index(
        'ix_user_subagent_runs_subagent_conversation_id',
        table_name='user_subagent_runs',
    )
    op.drop_table('user_subagent_runs')
