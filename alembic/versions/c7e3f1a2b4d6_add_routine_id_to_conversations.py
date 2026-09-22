"""add routine_id to conversations

Revision ID: c7e3f1a2b4d6
Revises: a91042ecde3b
Create Date: 2026-02-20 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e3f1a2b4d6'
down_revision: Union[str, Sequence[str], None] = 'a91042ecde3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add routine_id column to conversations."""
    op.add_column('conversations', sa.Column('routine_id', sa.String(length=36), nullable=True))
    op.create_index('ix_conversations_routine_id', 'conversations', ['routine_id'], unique=False)
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.create_foreign_key(
            'fk_conversations_routine_id',
            'routines',
            ['routine_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    """Remove routine_id from conversations."""
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.drop_constraint('fk_conversations_routine_id', type_='foreignkey')
    op.drop_index('ix_conversations_routine_id', table_name='conversations')
    op.drop_column('conversations', 'routine_id')
