"""add auto_title column to conversations

Revision ID: 8fb2c1a4d7e5
Revises: 3b9d4f7c2e10
Create Date: 2026-05-04 00:00:00.000000

Caches the auto-derived title (slice of the first user message in
chat_history.json) so the sidebar list query no longer has to open every
conversation's JSON file. ``custom_name`` still wins when set; this
column only matters for the ``custom_name IS NULL`` rows that previously
forced a per-row file read.

The column is nullable. Existing rows are filled in lazily by the FastAPI
lifespan startup hook (``_backfill_conversation_auto_title``) which mirrors
the pre-existing ``last_message_seq`` backfill pattern. The list endpoint
also falls back to a one-shot file read when a row's ``auto_title`` is
NULL but its ``last_message_seq > 0``, so a fresh deploy is correct
before the backfill finishes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8fb2c1a4d7e5'
down_revision: Union[str, Sequence[str], None] = '3b9d4f7c2e10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add auto_title column to conversations."""
    op.add_column(
        'conversations',
        sa.Column('auto_title', sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    """Remove auto_title from conversations."""
    op.drop_column('conversations', 'auto_title')
