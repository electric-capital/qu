"""add projects table and conversations.project_id

Revision ID: f09302acbdf1
Revises: b3f9a1c2d4e5
Create Date: 2026-02-19 20:22:42.423177

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f09302acbdf1'
down_revision: Union[str, Sequence[str], None] = 'b3f9a1c2d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add projects table and conversations.project_id FK."""
    # 1. Create the projects table
    op.create_table(
        'projects',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('guide', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # 2. Index on user_id for fast per-user listing
    op.create_index('ix_projects_user_id', 'projects', ['user_id'], unique=False)

    # 3. Composite unique index on (user_id, name) to prevent duplicate names per user
    op.create_index('ix_projects_user_id_name', 'projects', ['user_id', 'name'], unique=True)

    # 4. Add nullable project_id column to conversations
    op.add_column('conversations', sa.Column('project_id', sa.String(length=36), nullable=True))

    # 5. Index on project_id for fast per-project conversation listing
    op.create_index('ix_conversations_project_id', 'conversations', ['project_id'], unique=False)

    # 6. FK constraint from conversations.project_id to projects.id with CASCADE
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.create_foreign_key(
            'fk_conversations_project_id',
            'projects',
            ['project_id'],
            ['id'],
            ondelete='CASCADE',
        )


def downgrade() -> None:
    """Remove projects table and conversations.project_id FK."""
    # Remove FK and column from conversations
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.drop_constraint('fk_conversations_project_id', type_='foreignkey')

    op.drop_index('ix_conversations_project_id', table_name='conversations')
    op.drop_column('conversations', 'project_id')

    # Drop projects table and its indexes
    op.drop_index('ix_projects_user_id_name', table_name='projects')
    op.drop_index('ix_projects_user_id', table_name='projects')
    op.drop_table('projects')
