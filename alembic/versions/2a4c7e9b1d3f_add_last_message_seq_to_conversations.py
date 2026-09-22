"""add last_message_seq to conversations

Revision ID: 2a4c7e9b1d3f
Revises: c1f2a3d4e5b6
Create Date: 2026-04-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a4c7e9b1d3f'
down_revision: Union[str, Sequence[str], None] = 'c1f2a3d4e5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add last_message_seq column to conversations.

    Stores the high-water mark of the per-conversation monotonic ``seq``
    stamped on each message in chat_history.json. The column is a fast-read
    cache for the persistent WS subscribe handler; the JSON file remains
    the source of truth for individual seq stamps.

    Default 0 covers existing rows; the FastAPI lifespan startup hook then
    backfills from disk for any row whose chat_history.json carries
    messages.
    """
    op.add_column(
        'conversations',
        sa.Column(
            'last_message_seq',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
    )


def downgrade() -> None:
    """Remove last_message_seq from conversations."""
    op.drop_column('conversations', 'last_message_seq')
