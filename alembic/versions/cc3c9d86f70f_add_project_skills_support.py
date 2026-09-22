"""add project skills support

Revision ID: cc3c9d86f70f
Revises: 503d2eb4046f
Create Date: 2026-03-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc3c9d86f70f'
down_revision: Union[str, Sequence[str], None] = '503d2eb4046f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add project_id column to skills table (nullable, no cascade on delete)
    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table('skills') as batch_op:
        batch_op.add_column(sa.Column('project_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_skills_project_id',
            'projects',
            ['project_id'], ['id'],
        )
        batch_op.create_index('ix_skills_project_id', ['project_id'], unique=False)

    # Create project_skill_autoloads table
    op.create_table('project_skill_autoloads',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('skill_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_project_skill_autoloads_project_id', 'project_skill_autoloads', ['project_id'], unique=False)
    op.create_index('ix_project_skill_autoloads_skill_id', 'project_skill_autoloads', ['skill_id'], unique=False)
    op.create_index('ix_project_skill_autoloads_project_id_skill_id', 'project_skill_autoloads', ['project_id', 'skill_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Drop project_skill_autoloads table
    op.drop_index('ix_project_skill_autoloads_project_id_skill_id', table_name='project_skill_autoloads')
    op.drop_index('ix_project_skill_autoloads_skill_id', table_name='project_skill_autoloads')
    op.drop_index('ix_project_skill_autoloads_project_id', table_name='project_skill_autoloads')
    op.drop_table('project_skill_autoloads')

    # Remove project_id from skills table
    with op.batch_alter_table('skills') as batch_op:
        batch_op.drop_index('ix_skills_project_id')
        batch_op.drop_constraint('fk_skills_project_id', type_='foreignkey')
        batch_op.drop_column('project_id')
