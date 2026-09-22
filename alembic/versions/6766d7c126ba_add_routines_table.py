"""add routines table

Revision ID: 6766d7c126ba
Revises: f09302acbdf1
Create Date: 2026-02-20 19:33:14.341969

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6766d7c126ba'
down_revision: Union[str, Sequence[str], None] = 'f09302acbdf1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add routines table."""
    op.create_table(
        'routines',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('guide_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['guide_id'], ['guides.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Index on project_id for fast per-project listing
    op.create_index('ix_routines_project_id', 'routines', ['project_id'], unique=False)

    # Index on user_id for fast per-user queries
    op.create_index('ix_routines_user_id', 'routines', ['user_id'], unique=False)

    # Composite unique index on (project_id, name) to prevent duplicate names per project
    op.create_index('ix_routines_project_id_name', 'routines', ['project_id', 'name'], unique=True)


def downgrade() -> None:
    """Remove routines table."""
    op.drop_index('ix_routines_project_id_name', table_name='routines')
    op.drop_index('ix_routines_user_id', table_name='routines')
    op.drop_index('ix_routines_project_id', table_name='routines')
    op.drop_table('routines')
