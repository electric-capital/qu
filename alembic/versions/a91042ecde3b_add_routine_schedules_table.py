"""add routine_schedules table

Revision ID: a91042ecde3b
Revises: 6766d7c126ba
Create Date: 2026-02-20 20:36:04.805806

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a91042ecde3b'
down_revision: Union[str, Sequence[str], None] = '6766d7c126ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add routine_schedules table."""
    op.create_table(
        'routine_schedules',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('routine_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('schedule_type', sa.String(length=20), nullable=False),
        sa.Column('daily_time_utc', sa.String(length=5), nullable=True),
        sa.Column('daily_time_local', sa.String(length=5), nullable=True),
        sa.Column('timezone', sa.String(length=64), nullable=True),
        sa.Column('hourly_minute', sa.Integer(), nullable=True),
        sa.Column('interval_minutes', sa.Integer(), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('last_run_started_at', sa.DateTime(), nullable=True),
        sa.Column('last_run_completed_at', sa.DateTime(), nullable=True),
        sa.Column('is_running', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('last_conversation_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['routine_id'], ['routines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Unique index on routine_id (enforces one-to-one)
    op.create_index('ix_routine_schedules_routine_id', 'routine_schedules', ['routine_id'], unique=True)

    # Index on user_id for fast per-user queries
    op.create_index('ix_routine_schedules_user_id', 'routine_schedules', ['user_id'], unique=False)

    # Index on is_enabled + schedule_type for scheduler polling
    op.create_index('ix_routine_schedules_enabled_type', 'routine_schedules', ['is_enabled', 'schedule_type'], unique=False)


def downgrade() -> None:
    """Remove routine_schedules table."""
    op.drop_index('ix_routine_schedules_enabled_type', table_name='routine_schedules')
    op.drop_index('ix_routine_schedules_user_id', table_name='routine_schedules')
    op.drop_index('ix_routine_schedules_routine_id', table_name='routine_schedules')
    op.drop_table('routine_schedules')
