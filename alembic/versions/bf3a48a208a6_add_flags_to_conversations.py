"""add flags column to conversations

Revision ID: bf3a48a208a6
Revises: 41e59b874c9d
Create Date: 2026-06-05 00:00:00.000000

Adds a per-conversation ``flags`` JSON column that stores the set of opt-in
behaviors enabled at the start of a conversation (parsed from a magic
``%%flags[...]`` first line; see chat/conversation_flags.py). The value is a
JSON array of enabled flag-name strings, e.g. ``["nested_subagents"]``.

The column is nullable with no backfill: NULL is treated as "no flags" (an
empty set) by application code, exactly like the ``origin`` NULL == "web"
precedent. Flags are start-of-conversation only, so existing rows need no
migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf3a48a208a6'
down_revision: Union[str, Sequence[str], None] = '41e59b874c9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add flags column to conversations."""
    op.add_column(
        'conversations',
        sa.Column('flags', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Remove flags from conversations."""
    op.drop_column('conversations', 'flags')
