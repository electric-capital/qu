"""add routine skill autoloads

Revision ID: 41e59b874c9d
Revises: 511e66a8b1fd
Create Date: 2026-05-20 17:24:33.374688

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41e59b874c9d'
down_revision: Union[str, Sequence[str], None] = '511e66a8b1fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'routine_skill_autoloads',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('routine_id', sa.String(length=36), nullable=False),
        sa.Column('skill_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['routine_id'], ['routines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_routine_skill_autoloads_routine_id',
        'routine_skill_autoloads', ['routine_id'], unique=False,
    )
    op.create_index(
        'ix_routine_skill_autoloads_skill_id',
        'routine_skill_autoloads', ['skill_id'], unique=False,
    )
    op.create_index(
        'ix_routine_skill_autoloads_routine_id_skill_id',
        'routine_skill_autoloads', ['routine_id', 'skill_id'], unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_routine_skill_autoloads_routine_id_skill_id',
        table_name='routine_skill_autoloads',
    )
    op.drop_index(
        'ix_routine_skill_autoloads_skill_id',
        table_name='routine_skill_autoloads',
    )
    op.drop_index(
        'ix_routine_skill_autoloads_routine_id',
        table_name='routine_skill_autoloads',
    )
    op.drop_table('routine_skill_autoloads')
