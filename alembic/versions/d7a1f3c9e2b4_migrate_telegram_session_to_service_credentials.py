"""migrate users.telegram_session to user_service_credentials rows

Revision ID: d7a1f3c9e2b4
Revises: c4e8a2d17f63
Create Date: 2026-09-18 00:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from config import encryption


# revision identifiers, used by Alembic.
revision: str = 'd7a1f3c9e2b4'
down_revision: Union[str, Sequence[str], None] = 'c4e8a2d17f63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The aad labels MUST match the EncryptedText / EncryptedJSON declarations
# (the dropped users column as it was declared in c4e8a2d17f63, and
# db/models.py for the destination), since the ORM verifies them on read.
_SOURCE_AAD = "users.telegram_session"
_DEST_AAD = "user_service_credentials.oauth_blob"


def _require_key() -> None:
    try:
        encryption.get_data_key()
    except encryption.EncryptionError as exc:
        raise RuntimeError(
            "Migration d7a1f3c9e2b4 re-encrypts the Telegram session under a "
            f"new column label and needs the encryption-at-rest key: {exc}"
        ) from exc


def _plaintext_session(raw) -> str | None:
    """The stored users.telegram_session value as plaintext (or None)."""
    if raw is None:
        return None
    if encryption.is_encrypted(raw):
        return encryption.decrypt_str(raw, _SOURCE_AAD)
    return raw  # legacy plaintext row (pre-c4e8a2d17f63 writer)


def _plaintext_blob(raw) -> dict | None:
    """A stored user_service_credentials.oauth_blob value as a dict (or None)."""
    if raw is None:
        return None
    parsed = json.loads(raw)
    if encryption.is_encrypted(parsed):
        parsed = encryption.decrypt_json(parsed, _DEST_AAD)
    return parsed if isinstance(parsed, dict) else None


def upgrade() -> None:
    """Move each user's Telethon session into the plugin credential table.

    Telegram is now the in-tree plugin with id "telegram"; its per-user
    session string moves from the dedicated ``users.telegram_session``
    column into a ``user_service_credentials`` row (service="telegram",
    ``oauth_blob`` = ``{"session": "<StringSession>"}``), after which the
    column is dropped. Both columns are encrypted at rest with a
    per-column associated-data label, so the value cannot be copied in
    SQL: each row is decrypted under the old label and re-encrypted under
    the new one in Python (a legacy plaintext value is accepted too).
    Existing rows win (idempotent under a partial prior run).

    The column drop deliberately uses SQLite's native ALTER TABLE ... DROP
    COLUMN (available since SQLite 3.35), NOT batch_alter_table: migrations
    run with PRAGMA foreign_keys=ON (alembic/env.py), and a batch-mode
    table recreate DROPs the old ``users`` table, whose implicit DELETE
    FROM fires ON DELETE CASCADE on every child table -- including the
    user_service_credentials rows this migration just wrote. (Same shape
    as the Slack migration f3a9c5d81b42.)
    """
    _require_key()
    bind = op.get_bind()

    rows = bind.execute(sa.text(
        "SELECT id, telegram_session FROM users WHERE telegram_session IS NOT NULL"
    )).all()
    for user_id, raw in rows:
        session = _plaintext_session(raw)
        if not session:
            continue
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM user_service_credentials "
                "WHERE user_id = :user_id AND service = 'telegram'"
            ),
            {"user_id": user_id},
        ).first()
        if exists is not None:
            continue
        blob = json.dumps(encryption.encrypt_json({"session": session}, _DEST_AAD))
        bind.execute(
            sa.text(
                "INSERT INTO user_service_credentials "
                "(user_id, service, oauth_blob, created_at, updated_at) "
                "VALUES (:user_id, 'telegram', :blob, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"user_id": user_id, "blob": blob},
        )

    op.drop_column('users', 'telegram_session')


def downgrade() -> None:
    """Restore the column and copy the sessions back (rows are removed)."""
    _require_key()
    bind = op.get_bind()

    op.add_column(
        'users',
        sa.Column('telegram_session', sa.Text(), nullable=True),
    )
    rows = bind.execute(sa.text(
        "SELECT user_id, oauth_blob FROM user_service_credentials "
        "WHERE service = 'telegram'"
    )).all()
    for user_id, raw in rows:
        blob = _plaintext_blob(raw) or {}
        session = blob.get("session")
        if not isinstance(session, str) or not session:
            continue
        bind.execute(
            sa.text("UPDATE users SET telegram_session = :value WHERE id = :user_id"),
            {"value": encryption.encrypt_str(session, _SOURCE_AAD), "user_id": user_id},
        )
    bind.execute(sa.text(
        "DELETE FROM user_service_credentials WHERE service = 'telegram'"
    ))
