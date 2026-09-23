"""qualify stored OpenRouter model ids with their provider instance

Revision ID: b8e4d2a7c1f5
Revises: d7a1f3c9e2b4
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e4d2a7c1f5'
down_revision: Union[str, Sequence[str], None] = 'd7a1f3c9e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The single pre-instances OpenRouter configuration became provider
# instance "openrouter" (config/inference_providers.py
# LEGACY_OPENROUTER_INSTANCE_ID); models served by an instance are stored
# as "<instance_id>:<wire_id>".
_PREFIX = "openrouter:"

# A bare OpenRouter id contains a "/" (vendor/model) and has no instance
# qualifier: either no ":" at all, or its first ":" comes AFTER the "/"
# (OpenRouter's own ":variant" suffixes, e.g. "vendor/model:free"). Vertex
# ids never contain "/", so they are untouched.
_BARE = "instr({col}, '/') > 0 AND (instr({col}, ':') = 0 OR instr({col}, ':') > instr({col}, '/'))"


def _bare(col: str) -> str:
    return _BARE.format(col=col)


def upgrade() -> None:
    """Prefix bare OpenRouter model ids in every column that selects a model.

    ``conversations.model`` and ``routines.model`` are plain TEXT columns;
    the two per-user defaults live inside the ``users.settings`` JSON
    (``default_model`` for the web composer, ``slack_default_model`` for
    Slack-driven conversations) and are rewritten in place with json_set.
    Idempotent: an already-qualified id has its ":" before its "/" and is
    skipped. Historical ``llm_calls_openrouter.model`` rows are left as-is
    (pricing strips the qualifier before looking a model up).
    """
    for table in ("conversations", "routines"):
        op.execute(
            f"UPDATE {table} SET model = '{_PREFIX}' || model "
            f"WHERE model IS NOT NULL AND {_bare('model')}"
        )
    for key in ("default_model", "slack_default_model"):
        expr = f"json_extract(settings, '$.{key}')"
        op.execute(
            "UPDATE users SET settings = json_set(settings, "
            f"'$.{key}', '{_PREFIX}' || {expr}) "
            f"WHERE settings IS NOT NULL AND json_valid(settings) "
            f"AND json_type(settings, '$.{key}') = 'text' AND {_bare(expr)}"
        )


def downgrade() -> None:
    """Strip the ``openrouter:`` qualifier again (other instances' ids are
    left qualified -- the pre-instances schema has no way to express them)."""
    for table in ("conversations", "routines"):
        op.execute(
            f"UPDATE {table} SET model = substr(model, {len(_PREFIX) + 1}) "
            f"WHERE model LIKE '{_PREFIX}%'"
        )
    for key in ("default_model", "slack_default_model"):
        expr = f"json_extract(settings, '$.{key}')"
        op.execute(
            "UPDATE users SET settings = json_set(settings, "
            f"'$.{key}', substr({expr}, {len(_PREFIX) + 1})) "
            f"WHERE settings IS NOT NULL AND json_valid(settings) "
            f"AND {expr} LIKE '{_PREFIX}%'"
        )
