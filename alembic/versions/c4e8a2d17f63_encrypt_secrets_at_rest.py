"""encrypt stored secrets at rest

Revision ID: c4e8a2d17f63
Revises: f3a9c5d81b42
Create Date: 2026-09-03 00:00:00.000000

"""
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from config import encryption


# revision identifiers, used by Alembic.
revision: str = 'c4e8a2d17f63'
down_revision: Union[str, Sequence[str], None] = 'f3a9c5d81b42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, pk, [(column, kind, aad)]) -- kind is "text" or "json"; the aad
# labels MUST match the EncryptedText/EncryptedJSON declarations in
# db/models.py, since the ORM verifies them on every read.
_SECRET_COLUMNS = [
    ("users", "id", [
        ("api_key", "text", "users.api_key"),
        ("google_oauth", "json", "users.google_oauth"),
        ("google_services_oauth", "json", "users.google_services_oauth"),
        ("telegram_session", "text", "users.telegram_session"),
        ("airtable_token", "text", "users.airtable_token"),
        ("ramp_oauth", "json", "users.ramp_oauth"),
    ]),
    ("user_service_credentials", "id", [
        ("secret", "text", "user_service_credentials.secret"),
        ("oauth_blob", "json", "user_service_credentials.oauth_blob"),
    ]),
]


def _require_key() -> None:
    try:
        encryption.get_data_key()
    except encryption.EncryptionError as exc:
        raise RuntimeError(
            "Migration c4e8a2d17f63 encrypts stored secrets and needs the "
            f"encryption-at-rest key: {exc}"
        ) from exc


def _backup_database(bind) -> Path | None:
    """Copy the SQLite file beside itself before touching any row.

    Restoring is "stop the server, move the file back, run the previous
    release" -- the rollback path for deployments that skipped releases and
    have no downgrade password handy. Uses the sqlite3 online-backup API
    (consistent even with WAL sidecars). Skipped for in-memory databases.
    """
    db_file = bind.engine.url.database
    if not db_file or db_file == ":memory:":
        return None
    src_path = Path(db_file)
    if not src_path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = src_path.with_name(f"{src_path.name}.pre-encryption-{stamp}.bak")
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    try:
        shutil.copymode(src_path, backup_path)
    except OSError:
        pass
    return backup_path


def _is_json_encrypted(raw: str) -> bool:
    # A JSON column's encrypted form is the JSON string literal of the envelope.
    return raw.startswith('"' + encryption.ENVELOPE_PREFIX)


def _encrypt_value(raw, kind: str, aad: str):
    """Return the encrypted stored form of a raw column value (or None)."""
    if raw is None:
        return None
    if kind == "text":
        if encryption.is_encrypted(raw):
            return raw
        return encryption.encrypt_str(raw, aad)
    # json
    if _is_json_encrypted(raw):
        return raw
    parsed = json.loads(raw)
    if parsed is None:
        return None
    return json.dumps(encryption.encrypt_json(parsed, aad))


def _decrypt_value(raw, kind: str, aad: str):
    """Return the plaintext stored form of a raw column value (or None)."""
    if raw is None:
        return None
    if kind == "text":
        if encryption.is_encrypted(raw):
            return encryption.decrypt_str(raw, aad)
        return raw
    if _is_json_encrypted(raw):
        return json.dumps(encryption.decrypt_json(json.loads(raw), aad))
    return raw


def _rewrite_rows(bind, transform, api_key_hash_fn=None) -> None:
    """Apply ``transform(raw, kind, aad)`` to every secret column; on the
    users table also set ``api_key_hash`` from ``api_key_hash_fn(raw)``."""
    for table, pk, columns in _SECRET_COLUMNS:
        names = [c[0] for c in columns]
        rows = bind.execute(
            sa.text(f"SELECT {pk}, {', '.join(names)} FROM {table}")
        ).mappings().all()
        for row in rows:
            updates = {}
            for name, kind, aad in columns:
                new_value = transform(row[name], kind, aad)
                if new_value != row[name]:
                    updates[name] = new_value
            if table == "users" and api_key_hash_fn is not None:
                digest = api_key_hash_fn(row["api_key"])
                if digest is not None:
                    updates["api_key_hash"] = digest
            if not updates:
                continue
            assignments = ", ".join(f"{name} = :{name}" for name in updates)
            bind.execute(
                sa.text(f"UPDATE {table} SET {assignments} WHERE {pk} = :pk"),
                {**updates, "pk": row[pk]},
            )


def _plaintext_api_key(raw) -> str | None:
    if raw is None:
        return None
    if encryption.is_encrypted(raw):
        return encryption.decrypt_str(raw, "users.api_key")
    return raw


def upgrade() -> None:
    """Encrypt every stored secret in place and add ``users.api_key_hash``.

    Single-step by design: deployments may jump several releases, so there
    is no plaintext-fallback release in between. Instead the migration (a)
    writes a timestamped ``quest.db.pre-encryption-*.bak`` copy first,
    (b) is idempotent -- values already in the ``qenc1:`` envelope are
    skipped, so an interrupted run can simply be re-run -- and (c) the ORM
    column types tolerate plaintext on read (db/encrypted_types.py) for any
    row a still-running pre-upgrade process wrote afterwards.

    Columns keep their declared SQL types (the envelope is text; JSON
    columns hold the JSON string literal of the envelope), so no ALTER of
    the existing columns is needed. The only schema change is the new
    ``api_key_hash`` lookup column (SHA-256 of the plaintext key), because
    the random-nonce ciphertext in ``api_key`` cannot be queried by value.
    """
    _require_key()
    bind = op.get_bind()

    has_rows = any(
        bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None
        for table, _pk, _cols in _SECRET_COLUMNS
    )
    if has_rows:
        _backup_database(bind)

    op.add_column('users', sa.Column('api_key_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_users_api_key_hash', 'users', ['api_key_hash'], unique=True)

    def api_key_hash(raw_api_key):
        plain = _plaintext_api_key(raw_api_key)
        return encryption.hash_api_key(plain) if plain else None

    _rewrite_rows(bind, _encrypt_value, api_key_hash_fn=api_key_hash)


def downgrade() -> None:
    """Decrypt every secret back to plaintext and drop ``api_key_hash``."""
    _require_key()
    bind = op.get_bind()

    _rewrite_rows(bind, _decrypt_value)
    # Native DROP COLUMN (SQLite >= 3.35), not batch mode: see the note in
    # f3a9c5d81b42 about batch recreates cascading child-row deletes.
    op.drop_index('ix_users_api_key_hash', table_name='users')
    op.drop_column('users', 'api_key_hash')
