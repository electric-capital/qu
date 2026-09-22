"""add llm_calls_openrouter table

Revision ID: b3e7f2a91c04
Revises: a7d91c40e5b2
Create Date: 2026-08-21 00:00:00.000000

Third per-provider raw LLM call analytics table, for models served via the
OpenRouter backend. Same shared dimension/trailing columns as
``llm_calls_gemini`` / ``llm_calls_anthropic`` (05cdf20e3f2f); native usage
columns mirror the OpenAI-compatible usage object with the two nested
detail objects flattened (``cached_prompt_tokens`` <-
``prompt_tokens_details.cached_tokens``, ``reasoning_tokens`` <-
``completion_tokens_details.reasoning_tokens``). No backfill -- the
backend is new, so there is no historical data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e7f2a91c04'
down_revision: Union[str, Sequence[str], None] = 'a7d91c40e5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the llm_calls_openrouter raw analytics table."""
    op.create_table(
        'llm_calls_openrouter',
        # Shared dimension columns (same as the sibling raw tables).
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('call_type', sa.String(length=20), nullable=False),
        sa.Column('agent_name', sa.String(length=255), nullable=True),
        sa.Column('backend', sa.String(length=20), nullable=True),
        sa.Column('level', sa.Integer(), server_default='1', nullable=False),
        # Native OpenRouter usage fields (NULL = provider did not populate).
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('cached_prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('reasoning_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        # Shared trailing columns.
        sa.Column('raw_usage', sa.JSON(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_calls_openrouter_conversation_id', 'llm_calls_openrouter', ['conversation_id'], unique=False)
    op.create_index('ix_llm_calls_openrouter_user_id', 'llm_calls_openrouter', ['user_id'], unique=False)
    op.create_index('ix_llm_calls_openrouter_created_at', 'llm_calls_openrouter', ['created_at'], unique=False)


def downgrade() -> None:
    """Drop the llm_calls_openrouter table (analytics rows are lost)."""
    op.drop_index('ix_llm_calls_openrouter_created_at', table_name='llm_calls_openrouter')
    op.drop_index('ix_llm_calls_openrouter_user_id', table_name='llm_calls_openrouter')
    op.drop_index('ix_llm_calls_openrouter_conversation_id', table_name='llm_calls_openrouter')
    op.drop_table('llm_calls_openrouter')
