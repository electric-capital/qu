"""migrate users.twitter_oauth to user_service_credentials rows

Revision ID: e2f8a4c61b93
Revises: b8d41f6a9c27
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f8a4c61b93'
down_revision: Union[str, Sequence[str], None] = 'b8d41f6a9c27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Move per-user Twitter/X OAuth blobs into the plugin credential table.

    Twitter/X is now the in-tree plugin with id "twitter" (oauth-kind
    user connection); its per-user token JSON moves from the dedicated
    ``users.twitter_oauth`` column into a ``user_service_credentials``
    row (service="twitter", token JSON in ``oauth_blob``), after which
    the column is dropped. Existing rows win (idempotent under a partial
    prior run). Both columns are SQLAlchemy JSON (TEXT in SQLite), so the
    value copies verbatim -- the blob shape (access_token, refresh_token,
    expires_at, scope, token_type, authorized_at) is read as-is by
    plugins/twitter/upstream.py.

    The column drop deliberately uses SQLite's native ALTER TABLE ... DROP
    COLUMN (available since SQLite 3.35), NOT batch_alter_table: migrations
    run with PRAGMA foreign_keys=ON (alembic/env.py), and a batch-mode
    table recreate DROPs the old ``users`` table, whose implicit DELETE
    FROM fires ON DELETE CASCADE on every child table -- including the
    user_service_credentials rows this migration just wrote. (Same shape
    as the github migration a7c3e91b52d8.)
    """
    op.execute(sa.text(
        "INSERT INTO user_service_credentials "
        "(user_id, service, oauth_blob, created_at, updated_at) "
        "SELECT u.id, 'twitter', u.twitter_oauth, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM users u "
        "WHERE u.twitter_oauth IS NOT NULL "
        "AND NOT EXISTS ("
        "    SELECT 1 FROM user_service_credentials usc "
        "    WHERE usc.user_id = u.id AND usc.service = 'twitter'"
        ")"
    ))
    op.drop_column('users', 'twitter_oauth')


def downgrade() -> None:
    """Restore the column and copy the blobs back (rows are removed)."""
    op.add_column(
        'users',
        sa.Column('twitter_oauth', sa.JSON(), nullable=True),
    )
    op.execute(sa.text(
        "UPDATE users SET twitter_oauth = ("
        "    SELECT usc.oauth_blob FROM user_service_credentials usc "
        "    WHERE usc.user_id = users.id AND usc.service = 'twitter'"
        ")"
    ))
    op.execute(sa.text(
        "DELETE FROM user_service_credentials WHERE service = 'twitter'"
    ))
