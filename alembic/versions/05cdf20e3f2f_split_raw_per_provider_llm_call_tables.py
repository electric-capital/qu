"""split raw per-provider llm call tables

Revision ID: 05cdf20e3f2f
Revises: f058b82bb1e2
Create Date: 2026-07-06 22:46:17.163131

Replaces the single coalesced ``gemini_api_calls`` analytics log with two
per-provider raw tables (``llm_calls_gemini`` / ``llm_calls_anthropic``)
whose usage columns mirror each provider's NATIVE fields verbatim. One
revision: create both tables, backfill ALL historical rows, then DROP
``gemini_api_calls`` (no frozen archival copy -- reversibility lives in
downgrade()).

Backfill row routing: a row is Anthropic when ``provider = 'anthropic' OR
(provider IS NULL AND model LIKE 'claude%')`` (legacy rows predating
f058b82bb1e2 have NULL provider; every Anthropic model id starts with
"claude"); otherwise Gemini. The predicate partitions the old table exactly.

Native columns come from ``json_extract(raw_usage, ...)`` where present
(exact -- all rows since 2026-06-05). Legacy rows (NULL raw_usage) fall back
to the coalesced columns:

- Gemini: prompt <- input_tokens, candidates <- output_tokens,
  cached_content <- cached_tokens (exact -- the Gemini coalesced columns were
  verbatim copies of these native fields); thoughts / tool_use_prompt /
  total stay NULL (unknowable).
- Anthropic: input/output copied; cache_read_input_tokens <- cached_tokens
  is an APPROXIMATION -- legacy cached_tokens merged read+creation, so
  attributing it all to cache_read slightly under-estimates cost (creation
  bills 1.25x vs read ~0.1x); the merged total is exact, so context-tier
  deduction is unaffected. cache_creation_input_tokens stays NULL.
- The Anthropic cache-creation TTL split columns (5m/1h) are NULL for ALL
  backfilled rows -- raw_usage never captured them before this change.

Downgrade recreates the ``gemini_api_calls`` schema as of f058b82bb1e2 and
reverse-backfills the coalesced columns from the native ones (exact -- they
were always derivable). Two caveats vs the original data: pre-f058b82bb1e2
rows come back with ``provider`` populated instead of NULL, and original
``id`` values are not preserved (new autoincrement).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '05cdf20e3f2f'
down_revision: Union[str, Sequence[str], None] = 'f058b82bb1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Rows routed to llm_calls_anthropic during backfill; the complement goes to
# llm_calls_gemini. Legacy (pre-f058b82bb1e2) rows have NULL provider -- the
# COALESCE keeps the predicate NULL-safe so ``NOT (...)`` selects the exact
# complement (a bare ``provider = 'anthropic'`` is NULL for those rows, and
# NOT NULL would silently drop them from both partitions).
_ANTHROPIC_ROW_PREDICATE = (
    "COALESCE(provider, '') = 'anthropic' "
    "OR (provider IS NULL AND model LIKE 'claude%')"
)


def _shared_dimension_columns() -> list[sa.Column]:
    """Leading dimension columns shared by both raw tables (fresh per call)."""
    return [
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('call_type', sa.String(length=20), nullable=False),
        sa.Column('agent_name', sa.String(length=255), nullable=True),
        sa.Column('backend', sa.String(length=20), nullable=True),
        sa.Column('level', sa.Integer(), server_default='1', nullable=False),
    ]


def _shared_trailing_columns() -> list[sa.Column]:
    """Trailing columns shared by both raw tables (fresh per call)."""
    return [
        sa.Column('raw_usage', sa.JSON(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    """Create the raw tables, backfill from gemini_api_calls, drop it."""
    op.create_table(
        'llm_calls_gemini',
        *_shared_dimension_columns(),
        # Native Gemini usage fields (NULL = SDK did not populate).
        sa.Column('prompt_token_count', sa.Integer(), nullable=True),
        sa.Column('candidates_token_count', sa.Integer(), nullable=True),
        sa.Column('cached_content_token_count', sa.Integer(), nullable=True),
        sa.Column('thoughts_token_count', sa.Integer(), nullable=True),
        sa.Column('tool_use_prompt_token_count', sa.Integer(), nullable=True),
        sa.Column('total_token_count', sa.Integer(), nullable=True),
        *_shared_trailing_columns(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_calls_gemini_conversation_id', 'llm_calls_gemini', ['conversation_id'], unique=False)
    op.create_index('ix_llm_calls_gemini_user_id', 'llm_calls_gemini', ['user_id'], unique=False)
    op.create_index('ix_llm_calls_gemini_created_at', 'llm_calls_gemini', ['created_at'], unique=False)

    op.create_table(
        'llm_calls_anthropic',
        *_shared_dimension_columns(),
        # Native Anthropic usage fields (NULL = SDK did not populate).
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('cache_read_input_tokens', sa.Integer(), nullable=True),
        sa.Column('cache_creation_input_tokens', sa.Integer(), nullable=True),
        sa.Column('cache_creation_5m_input_tokens', sa.Integer(), nullable=True),
        sa.Column('cache_creation_1h_input_tokens', sa.Integer(), nullable=True),
        *_shared_trailing_columns(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_calls_anthropic_conversation_id', 'llm_calls_anthropic', ['conversation_id'], unique=False)
    op.create_index('ix_llm_calls_anthropic_user_id', 'llm_calls_anthropic', ['user_id'], unique=False)
    op.create_index('ix_llm_calls_anthropic_created_at', 'llm_calls_anthropic', ['created_at'], unique=False)

    # Backfill Gemini rows. json_extract returns NULL for missing keys and
    # NULL raw_usage, so COALESCE picks the exact raw value when captured and
    # otherwise the coalesced-column fallback (which for Gemini was a verbatim
    # copy of the native field). thoughts/tool_use/total have no fallback.
    op.execute(f"""
        INSERT INTO llm_calls_gemini (
            conversation_id, user_id, model, call_type, agent_name, backend,
            level,
            prompt_token_count, candidates_token_count,
            cached_content_token_count, thoughts_token_count,
            tool_use_prompt_token_count, total_token_count,
            raw_usage, duration_ms, created_at
        )
        SELECT
            conversation_id, user_id, model, call_type, agent_name, backend,
            level,
            COALESCE(json_extract(raw_usage, '$.prompt_token_count'), input_tokens),
            COALESCE(json_extract(raw_usage, '$.candidates_token_count'), output_tokens),
            COALESCE(json_extract(raw_usage, '$.cached_content_token_count'), cached_tokens),
            json_extract(raw_usage, '$.thoughts_token_count'),
            json_extract(raw_usage, '$.tool_use_prompt_token_count'),
            json_extract(raw_usage, '$.total_token_count'),
            raw_usage, duration_ms, created_at
        FROM gemini_api_calls
        WHERE NOT ({_ANTHROPIC_ROW_PREDICATE})
        ORDER BY id
    """)

    # Backfill Anthropic rows. Legacy fallback attributes the merged
    # cached_tokens entirely to cache_read (approximation; see module
    # docstring); TTL-split columns are NULL for all backfilled rows.
    op.execute(f"""
        INSERT INTO llm_calls_anthropic (
            conversation_id, user_id, model, call_type, agent_name, backend,
            level,
            input_tokens, output_tokens,
            cache_read_input_tokens, cache_creation_input_tokens,
            cache_creation_5m_input_tokens, cache_creation_1h_input_tokens,
            raw_usage, duration_ms, created_at
        )
        SELECT
            conversation_id, user_id, model, call_type, agent_name, backend,
            level,
            COALESCE(json_extract(raw_usage, '$.input_tokens'), input_tokens),
            COALESCE(json_extract(raw_usage, '$.output_tokens'), output_tokens),
            COALESCE(json_extract(raw_usage, '$.cache_read_input_tokens'), cached_tokens),
            json_extract(raw_usage, '$.cache_creation_input_tokens'),
            json_extract(raw_usage, '$.cache_creation_5m_input_tokens'),
            json_extract(raw_usage, '$.cache_creation_1h_input_tokens'),
            raw_usage, duration_ms, created_at
        FROM gemini_api_calls
        WHERE {_ANTHROPIC_ROW_PREDICATE}
        ORDER BY id
    """)

    op.drop_index('ix_gemini_api_calls_user_id', table_name='gemini_api_calls')
    op.drop_index('ix_gemini_api_calls_created_at', table_name='gemini_api_calls')
    op.drop_index('ix_gemini_api_calls_conversation_id', table_name='gemini_api_calls')
    op.drop_table('gemini_api_calls')


def downgrade() -> None:
    """Recreate gemini_api_calls, reverse-backfill it, drop the raw tables."""
    # Schema as of f058b82bb1e2 (7d31c3fd5c83 base + a7eb1d002df0 cached_tokens
    # + f058b82bb1e2 provider/backend/level/raw_usage).
    op.create_table(
        'gemini_api_calls',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('call_type', sa.String(length=20), nullable=False),
        sa.Column('agent_name', sa.String(length=255), nullable=True),
        sa.Column('provider', sa.String(length=20), nullable=True),
        sa.Column('backend', sa.String(length=20), nullable=True),
        sa.Column('level', sa.Integer(), server_default='1', nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cached_tokens', sa.Integer(), nullable=False),
        sa.Column('raw_usage', sa.JSON(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_gemini_api_calls_conversation_id', 'gemini_api_calls', ['conversation_id'], unique=False)
    op.create_index('ix_gemini_api_calls_created_at', 'gemini_api_calls', ['created_at'], unique=False)
    op.create_index('ix_gemini_api_calls_user_id', 'gemini_api_calls', ['user_id'], unique=False)

    # Reverse-backfill: the coalesced columns were always derivable from the
    # native fields, so this is exact. provider comes back populated even for
    # rows that originally had NULL (pre-f058b82bb1e2); ids renumber.
    op.execute("""
        INSERT INTO gemini_api_calls (
            conversation_id, user_id, model, call_type, agent_name,
            provider, backend, level,
            input_tokens, output_tokens, cached_tokens,
            raw_usage, duration_ms, created_at
        )
        SELECT
            conversation_id, user_id, model, call_type, agent_name,
            'gemini', backend, level,
            COALESCE(prompt_token_count, 0),
            COALESCE(candidates_token_count, 0),
            COALESCE(cached_content_token_count, 0),
            raw_usage, duration_ms, created_at
        FROM llm_calls_gemini
        ORDER BY id
    """)
    op.execute("""
        INSERT INTO gemini_api_calls (
            conversation_id, user_id, model, call_type, agent_name,
            provider, backend, level,
            input_tokens, output_tokens, cached_tokens,
            raw_usage, duration_ms, created_at
        )
        SELECT
            conversation_id, user_id, model, call_type, agent_name,
            'anthropic', backend, level,
            COALESCE(input_tokens, 0),
            COALESCE(output_tokens, 0),
            COALESCE(cache_read_input_tokens, 0) + COALESCE(cache_creation_input_tokens, 0),
            raw_usage, duration_ms, created_at
        FROM llm_calls_anthropic
        ORDER BY id
    """)

    op.drop_index('ix_llm_calls_anthropic_created_at', table_name='llm_calls_anthropic')
    op.drop_index('ix_llm_calls_anthropic_user_id', table_name='llm_calls_anthropic')
    op.drop_index('ix_llm_calls_anthropic_conversation_id', table_name='llm_calls_anthropic')
    op.drop_table('llm_calls_anthropic')
    op.drop_index('ix_llm_calls_gemini_created_at', table_name='llm_calls_gemini')
    op.drop_index('ix_llm_calls_gemini_user_id', table_name='llm_calls_gemini')
    op.drop_index('ix_llm_calls_gemini_conversation_id', table_name='llm_calls_gemini')
    op.drop_table('llm_calls_gemini')
