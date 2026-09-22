"""migrate users.github_oauth to user_service_credentials rows

Revision ID: a7c3e91b52d8
Revises: d9f4b82a61c7
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e91b52d8'
down_revision: Union[str, Sequence[str], None] = 'd9f4b82a61c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Move per-user GitHub OAuth blobs into the plugin credential table.

    GitHub is now the in-tree plugin with id "github" (the first with an
    oauth-kind user connection); its per-user token JSON moves from the
    dedicated ``users.github_oauth`` column into a
    ``user_service_credentials`` row (service="github", token JSON in
    ``oauth_blob``), after which the column is dropped. Existing rows win
    (idempotent under a partial prior run). Both columns are SQLAlchemy
    JSON (TEXT in SQLite), so the value copies verbatim.

    The column drop deliberately uses SQLite's native ALTER TABLE ... DROP
    COLUMN (available since SQLite 3.35), NOT batch_alter_table: migrations
    run with PRAGMA foreign_keys=ON (alembic/env.py), and a batch-mode
    table recreate DROPs the old ``users`` table, whose implicit DELETE
    FROM fires ON DELETE CASCADE on every child table -- including the
    user_service_credentials rows this migration just wrote.
    """
    op.execute(sa.text(
        "INSERT INTO user_service_credentials "
        "(user_id, service, oauth_blob, created_at, updated_at) "
        "SELECT u.id, 'github', u.github_oauth, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM users u "
        "WHERE u.github_oauth IS NOT NULL "
        "AND NOT EXISTS ("
        "    SELECT 1 FROM user_service_credentials usc "
        "    WHERE usc.user_id = u.id AND usc.service = 'github'"
        ")"
    ))
    op.drop_column('users', 'github_oauth')


def downgrade() -> None:
    """Restore the column and copy the blobs back (rows are removed)."""
    op.add_column(
        'users',
        sa.Column('github_oauth', sa.JSON(), nullable=True),
    )
    op.execute(sa.text(
        "UPDATE users SET github_oauth = ("
        "    SELECT usc.oauth_blob FROM user_service_credentials usc "
        "    WHERE usc.user_id = users.id AND usc.service = 'github'"
        ")"
    ))
    op.execute(sa.text(
        "DELETE FROM user_service_credentials WHERE service = 'github'"
    ))
