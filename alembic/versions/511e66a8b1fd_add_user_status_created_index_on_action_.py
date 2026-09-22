"""add user status created index on action_requests

Revision ID: 511e66a8b1fd
Revises: a9e2be6b9caa
Create Date: 2026-05-20 16:09:50.481530

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '511e66a8b1fd'
down_revision: Union[str, Sequence[str], None] = 'a9e2be6b9caa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'ix_action_requests_user_status_created', 'action_requests',
        ['user_id', 'status', 'created_at'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_action_requests_user_status_created', table_name='action_requests')
