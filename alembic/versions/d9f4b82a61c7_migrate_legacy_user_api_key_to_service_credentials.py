"""migrate the legacy users.crm_api_key column to user_service_credentials rows

Revision ID: d9f4b82a61c7
Revises: c4e7a2d91f58
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9f4b82a61c7'
down_revision: Union[str, Sequence[str], None] = 'c4e7a2d91f58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Move legacy per-user service keys into the generic plugin credential table.

    A pre-plugin integration stored its per-user API key in a dedicated
    ``users.crm_api_key`` column; that key moves into a
    ``user_service_credentials`` row (service="crm", key in ``secret``),
    after which the column is dropped. Existing rows win (idempotent under
    a partial prior run).

    The column drop deliberately uses SQLite's native ALTER TABLE ... DROP
    COLUMN (available since SQLite 3.35), NOT batch_alter_table: migrations
    run with PRAGMA foreign_keys=ON (alembic/env.py), and a batch-mode
    table recreate DROPs the old ``users`` table, whose implicit DELETE
    FROM fires ON DELETE CASCADE on every child table -- including the
    user_service_credentials rows this migration just wrote.
    """
    op.execute(sa.text(
        "INSERT INTO user_service_credentials "
        "(user_id, service, secret, created_at, updated_at) "
        "SELECT u.id, 'crm', u.crm_api_key, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM users u "
        "WHERE u.crm_api_key IS NOT NULL "
        "AND NOT EXISTS ("
        "    SELECT 1 FROM user_service_credentials usc "
        "    WHERE usc.user_id = u.id AND usc.service = 'crm'"
        ")"
    ))
    op.drop_column('users', 'crm_api_key')


def downgrade() -> None:
    """Restore the column and copy the keys back (rows are removed)."""
    op.add_column(
        'users',
        sa.Column('crm_api_key', sa.String(length=128), nullable=True),
    )
    op.execute(sa.text(
        "UPDATE users SET crm_api_key = ("
        "    SELECT usc.secret FROM user_service_credentials usc "
        "    WHERE usc.user_id = users.id AND usc.service = 'crm'"
        ")"
    ))
    op.execute(sa.text(
        "DELETE FROM user_service_credentials WHERE service = 'crm'"
    ))
