"""add memories updated_at and archived

Revision ID: 861a7abe9a23
Revises: 94c49d92fea7
Create Date: 2026-02-11 22:10:42.036395

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '861a7abe9a23'
down_revision: Union[str, Sequence[str], None] = '94c49d92fea7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('memories', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('memories', sa.Column('archived', sa.Boolean(), server_default='0', nullable=False))
    op.create_index('ix_memories_user_email_archived', 'memories', ['user_email', 'archived'])


def downgrade() -> None:
    op.drop_index('ix_memories_user_email_archived', table_name='memories')
    op.drop_column('memories', 'archived')
    op.drop_column('memories', 'updated_at')
