"""Backfill routine and schedule updated_at for legacy NULL rows

Revision ID: a9e2be6b9caa
Revises: 8fb2c1a4d7e5
Create Date: 2026-05-11 00:00:00.000000

Routines and routine_schedules created before the optimistic-concurrency
guard landed have ``updated_at = NULL`` because ``create_routine`` /
``create_schedule`` never stamped the column. Seed the existing rows with
``created_at`` so the FE has a non-null baseline to compare on first edit
and the guard engages immediately rather than skipping the check on
back-compat grounds.

Schema-only no-op: no columns added or dropped.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9e2be6b9caa'
down_revision: Union[str, Sequence[str], None] = '8fb2c1a4d7e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill NULL updated_at columns from created_at."""
    op.execute(
        "UPDATE routines SET updated_at = created_at WHERE updated_at IS NULL"
    )
    op.execute(
        "UPDATE routine_schedules SET updated_at = created_at WHERE updated_at IS NULL"
    )


def downgrade() -> None:
    """No-op: backfill is data-only and not safely reversible."""
    pass
