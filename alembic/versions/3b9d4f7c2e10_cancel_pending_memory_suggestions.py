"""cancel pending memory_suggestion wait handles

Revision ID: 3b9d4f7c2e10
Revises: 2a4c7e9b1d3f
Create Date: 2026-04-29 00:00:00.000000

The ``suggest_memory`` tool, the dedicated ``memory_suggestion``
wait-handle kind, and the inline ``confirm_action`` UI were retired in
favour of a generic ``create_memory`` action_request handler. Any
``tool_wait_handles`` rows of kind ``memory_suggestion`` left over from
before the cutover have no live consumer, so flip every still-pending
row to ``cancelled`` so dangling ``wait_for_handles`` resumes wake up
cleanly with a "user did not act" signal instead of blocking forever.

This migration is a pure data migration -- the ``kind`` column is a
``String(50)`` and was never enum-constrained on the database side, so
no schema change is required. The migration is also idempotent: re-
running it on an already-cleaned DB does nothing because the WHERE
clause finds no matching rows.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3b9d4f7c2e10'
down_revision: Union[str, Sequence[str], None] = '2a4c7e9b1d3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Cancel any pending memory_suggestion wait-handle rows."""
    bind = op.get_bind()
    result = bind.exec_driver_sql(
        """
        UPDATE tool_wait_handles
           SET status = 'cancelled',
               resolved_at = CURRENT_TIMESTAMP
         WHERE kind = 'memory_suggestion'
           AND status = 'pending'
        """
    )
    # ``rowcount`` is best-effort across drivers; SQLite reports it
    # reliably. Surface the number so the deploy log shows what was
    # cleaned up.
    try:
        count = result.rowcount
    except Exception:
        count = -1
    if count and count > 0:
        print(
            f"[migrations] cancel_pending_memory_suggestions: "
            f"flipped {count} row(s) from pending to cancelled."
        )


def downgrade() -> None:
    """Irreversible. The original ``status`` value (``pending``) is not
    preserved separately, and there is no live ``memory_suggestion``
    consumer to flip rows back to in any case. No-op."""
    pass
