"""add OpenRouter reported cost columns to llm_calls_openrouter

Revision ID: f7d2a9c41e58
Revises: e5b2c8f14a37
Create Date: 2026-09-24 12:00:00.000000

OpenRouter reports the USD amount it charged for a request in the final
usage chunk when the request opts in (``usage: {"include": true}``). The
provider now captures it, and the analytics layer prefers it over the
list-price estimate. Existing rows keep NULL (no backfill is possible --
the amount is only known at request time), so they stay estimated.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7d2a9c41e58'
down_revision: Union[str, Sequence[str], None] = 'e5b2c8f14a37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'llm_calls_openrouter',
        sa.Column('cost', sa.Float(), nullable=True),
    )
    op.add_column(
        'llm_calls_openrouter',
        sa.Column('upstream_inference_cost', sa.Float(), nullable=True),
    )
    op.add_column(
        'llm_calls_openrouter',
        sa.Column('is_byok', sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('llm_calls_openrouter', 'is_byok')
    op.drop_column('llm_calls_openrouter', 'upstream_inference_cost')
    op.drop_column('llm_calls_openrouter', 'cost')
