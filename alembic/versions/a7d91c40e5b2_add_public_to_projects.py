"""add public column to projects

Revision ID: a7d91c40e5b2
Revises: b2c7e4f1a9d3
Create Date: 2026-08-21 00:00:00.000000

Adds a ``public`` boolean column to ``projects``. Public mode is chosen at
project creation time and is immutable afterwards (the update path never
reads it). Conversations in a public project run with internet-enabled
sandboxing and are cut off from every internal resource (skills, memories,
connectors, action requests) so a public project can never become a
data-exfiltration path for internal data. Public projects cannot have
routines or project skills (blocked at the API layer).

The column is NOT NULL with a server default of false: every existing
project is private, which preserves current behavior exactly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d91c40e5b2'
down_revision: Union[str, Sequence[str], None] = 'b2c7e4f1a9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add public column to projects."""
    op.add_column(
        'projects',
        sa.Column(
            'public', sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Remove public from projects."""
    op.drop_column('projects', 'public')
