"""migrate users.slack_oauth to user_service_credentials rows

Revision ID: f3a9c5d81b42
Revises: e2f8a4c61b93
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c5d81b42'
down_revision: Union[str, Sequence[str], None] = 'e2f8a4c61b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Move per-user Slack OAuth blobs into the plugin credential table.

    Slack is now the in-tree plugin with id "slack"; its per-user token
    JSON (access_token, default_team_id, user_id) moves from the
    dedicated ``users.slack_oauth`` column into a
    ``user_service_credentials`` row (service="slack", token JSON in
    ``oauth_blob``), after which the column is dropped. Existing rows win
    (idempotent under a partial prior run). Both columns are SQLAlchemy
    JSON (TEXT in SQLite), so the value copies verbatim -- including the
    ``$.user_id`` key the Socket Mode worker's reverse lookup
    (db/user_store.py get_user_by_slack_user_id) json_extracts.

    The column drop deliberately uses SQLite's native ALTER TABLE ... DROP
    COLUMN (available since SQLite 3.35), NOT batch_alter_table: migrations
    run with PRAGMA foreign_keys=ON (alembic/env.py), and a batch-mode
    table recreate DROPs the old ``users`` table, whose implicit DELETE
    FROM fires ON DELETE CASCADE on every child table -- including the
    user_service_credentials rows this migration just wrote. (Same shape
    as the GitHub migration a7c3e91b52d8.)
    """
    op.execute(sa.text(
        "INSERT INTO user_service_credentials "
        "(user_id, service, oauth_blob, created_at, updated_at) "
        "SELECT u.id, 'slack', u.slack_oauth, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM users u "
        "WHERE u.slack_oauth IS NOT NULL "
        "AND NOT EXISTS ("
        "    SELECT 1 FROM user_service_credentials usc "
        "    WHERE usc.user_id = u.id AND usc.service = 'slack'"
        ")"
    ))
    op.drop_column('users', 'slack_oauth')


def downgrade() -> None:
    """Restore the column and copy the blobs back (rows are removed)."""
    op.add_column(
        'users',
        sa.Column('slack_oauth', sa.JSON(), nullable=True),
    )
    op.execute(sa.text(
        "UPDATE users SET slack_oauth = ("
        "    SELECT usc.oauth_blob FROM user_service_credentials usc "
        "    WHERE usc.user_id = users.id AND usc.service = 'slack'"
        ")"
    ))
    op.execute(sa.text(
        "DELETE FROM user_service_credentials WHERE service = 'slack'"
    ))
