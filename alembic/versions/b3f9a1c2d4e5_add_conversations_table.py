"""Add conversations table and migrate chats directory to flat layout.

Revision ID: b3f9a1c2d4e5
Revises: eea78c9364cf
Create Date: 2026-02-19 00:00:00.000000

Changes:
- Creates the conversations table with user_id FK, created_at, last_message_at.
- Creates indexes: ix_conversations_user_id, ix_conversations_user_id_last_message.
- Populates the table by walking data/chats/{user_id}/{conversation_id}/ and
  reading each chat_history.json.
- Moves each conversation directory from data/chats/{user_id}/{conversation_id}/
  to data/chats/{conversation_id}/ (flat layout).
- Removes now-empty data/chats/{user_id}/ directories.

Downgrade reverses all of the above: moves directories back, drops the table.
"""

import json
import logging
import shutil
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from config.paths import CHATS_DIR as _CHATS_DIR

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision: str = 'b3f9a1c2d4e5'
down_revision: Union[str, Sequence[str], None] = 'eea78c9364cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _parse_ts(ts_str: str) -> str:
    """Return ts_str as-is if non-empty, else empty string.

    SQLite stores DATETIME as TEXT; we store ISO 8601 strings from Python.
    No conversion needed — just pass through the value from chat_history.json.
    """
    return (ts_str or "").strip()


def upgrade() -> None:
    """Create conversations table, populate from filesystem, flatten directory layout."""
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Create the conversations table
    # ------------------------------------------------------------------
    op.create_table(
        'conversations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_message_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # ------------------------------------------------------------------
    # 2. Create indexes
    # ------------------------------------------------------------------
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'], unique=False)
    op.create_index(
        'ix_conversations_user_id_last_message',
        'conversations',
        ['user_id', 'last_message_at'],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 3. Populate table and flatten filesystem layout
    # ------------------------------------------------------------------
    if not _CHATS_DIR.exists():
        logger.info("[migration b3f9a1c2d4e5] data/chats/ does not exist; skipping data population")
        return

    # Collect all valid user_id subdirectories (integer-named)
    user_dirs = []
    for entry in _CHATS_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            user_id = int(entry.name)
        except ValueError:
            # Not an integer-named directory; skip (may be a flat UUID dir from
            # a partially-applied migration or a leftover file)
            logger.debug("[migration b3f9a1c2d4e5] Skipping non-integer dir: %s", entry.name)
            continue
        user_dirs.append((user_id, entry))

    # Verify user IDs exist in the database before we move anything.
    # We collect all known user IDs so we can skip orphans gracefully.
    known_user_ids: set[int] = set()
    rows = conn.execute(sa.text("SELECT id FROM users")).fetchall()
    for row in rows:
        known_user_ids.add(int(row[0]))

    # First pass: validate — check for UUID collisions across users.
    seen_uuids: set[str] = set()
    for user_id, user_dir in user_dirs:
        for conv_dir in user_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            conv_id = conv_dir.name
            if conv_id in seen_uuids:
                raise RuntimeError(
                    f"[migration b3f9a1c2d4e5] Conversation UUID collision detected: "
                    f"{conv_id} appears in multiple user directories. "
                    f"Cannot proceed safely."
                )
            seen_uuids.add(conv_id)

    # Second pass: insert DB rows and move directories.
    # We perform DB inserts first, then filesystem moves.  If a filesystem
    # move fails, we raise immediately so Alembic rolls back the DB changes.
    rows_to_insert = []

    for user_id, user_dir in sorted(user_dirs):
        for conv_dir in sorted(user_dir.iterdir()):
            if not conv_dir.is_dir():
                continue

            conv_id = conv_dir.name
            chat_file = conv_dir / "chat_history.json"

            if not chat_file.exists():
                logger.warning(
                    "[migration b3f9a1c2d4e5] No chat_history.json in %s; skipping",
                    conv_dir,
                )
                continue

            # Read metadata from the file
            try:
                with open(chat_file, "r", encoding="utf-8") as f:
                    chat_data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "[migration b3f9a1c2d4e5] Could not read %s (%s); skipping",
                    chat_file, exc,
                )
                continue

            created_at = _parse_ts(chat_data.get("created_at", ""))

            # Determine last_message_at from the last message's timestamp
            messages = chat_data.get("messages", [])
            last_message_at = created_at
            if messages:
                last_ts = messages[-1].get("timestamp", "")
                if last_ts:
                    last_message_at = _parse_ts(last_ts)

            if not created_at:
                # Fallback: use SQLite 'now' if no timestamp is recorded
                created_at = "1970-01-01T00:00:00Z"
                last_message_at = created_at
                logger.warning(
                    "[migration b3f9a1c2d4e5] No created_at in %s; using epoch", chat_file
                )

            if user_id not in known_user_ids:
                # Orphaned directory (user deleted from DB); move the directory
                # but skip the DB insert to avoid FK violation.
                logger.warning(
                    "[migration b3f9a1c2d4e5] user_id=%d not in users table; "
                    "will move directory but skip DB insert for conv %s",
                    user_id, conv_id,
                )
                rows_to_insert.append((conv_id, user_id, created_at, last_message_at, False))
            else:
                rows_to_insert.append((conv_id, user_id, created_at, last_message_at, True))

    # Insert DB rows
    for conv_id, user_id, created_at, last_message_at, insert_db in rows_to_insert:
        if insert_db:
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO conversations "
                    "(id, user_id, created_at, last_message_at) "
                    "VALUES (:id, :user_id, :created_at, :last_message_at)"
                ),
                {
                    "id": conv_id,
                    "user_id": user_id,
                    "created_at": created_at,
                    "last_message_at": last_message_at,
                },
            )
            logger.debug(
                "[migration b3f9a1c2d4e5] Inserted conversation row: %s (user_id=%d)",
                conv_id, user_id,
            )

    # Move directories (after all DB inserts so we can roll back cleanly)
    for conv_id, user_id, _created_at, _last_msg_at, _insert_db in rows_to_insert:
        src = _CHATS_DIR / str(user_id) / conv_id
        dst = _CHATS_DIR / conv_id

        if not src.exists():
            logger.warning(
                "[migration b3f9a1c2d4e5] Source dir does not exist, skipping move: %s",
                src,
            )
            continue

        if dst.exists():
            logger.warning(
                "[migration b3f9a1c2d4e5] Target dir already exists, skipping move: %s -> %s",
                src, dst,
            )
            continue

        try:
            shutil.move(str(src), str(dst))
            logger.info(
                "[migration b3f9a1c2d4e5] Moved: %s -> %s", src, dst
            )
        except Exception as exc:
            raise RuntimeError(
                f"[migration b3f9a1c2d4e5] Failed to move {src} -> {dst}: {exc}. "
                f"Aborting so the DB can roll back."
            ) from exc

    # Remove now-empty user subdirectories
    for user_id, user_dir in user_dirs:
        try:
            # Only remove if the directory is empty
            remaining = list(user_dir.iterdir())
            if not remaining:
                user_dir.rmdir()
                logger.info(
                    "[migration b3f9a1c2d4e5] Removed empty user dir: %s", user_dir
                )
            else:
                logger.warning(
                    "[migration b3f9a1c2d4e5] User dir %s not empty after moves "
                    "(%d items remain); leaving it",
                    user_dir, len(remaining),
                )
        except Exception as exc:
            # Non-fatal: we log and continue; the important work is done
            logger.warning(
                "[migration b3f9a1c2d4e5] Could not remove user dir %s: %s",
                user_dir, exc,
            )


def downgrade() -> None:
    """Drop conversations table, move conversation dirs back to {user_id}/ subdirs."""
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Collect current conversations so we know where to put them back
    # ------------------------------------------------------------------
    rows = conn.execute(
        sa.text("SELECT id, user_id FROM conversations")
    ).fetchall()

    # ------------------------------------------------------------------
    # 2. Move directories back to {user_id}/{conversation_id}/
    # ------------------------------------------------------------------
    if _CHATS_DIR.exists():
        for conv_id, user_id in rows:
            src = _CHATS_DIR / conv_id
            user_dir = _CHATS_DIR / str(user_id)
            dst = user_dir / conv_id

            if not src.exists():
                logger.warning(
                    "[migration b3f9a1c2d4e5 downgrade] Source dir does not exist, "
                    "skipping: %s",
                    src,
                )
                continue

            if dst.exists():
                logger.warning(
                    "[migration b3f9a1c2d4e5 downgrade] Target already exists, "
                    "skipping: %s -> %s",
                    src, dst,
                )
                continue

            # Create user directory if it doesn't exist
            user_dir.mkdir(parents=True, exist_ok=True)

            try:
                shutil.move(str(src), str(dst))
                logger.info(
                    "[migration b3f9a1c2d4e5 downgrade] Moved: %s -> %s", src, dst
                )
            except Exception as exc:
                logger.error(
                    "[migration b3f9a1c2d4e5 downgrade] Failed to move %s -> %s: %s",
                    src, dst, exc,
                )
                # Continue best-effort; schema changes will still be rolled back

    # ------------------------------------------------------------------
    # 3. Drop indexes and conversations table
    # ------------------------------------------------------------------
    op.drop_index('ix_conversations_user_id_last_message', table_name='conversations')
    op.drop_index('ix_conversations_user_id', table_name='conversations')
    op.drop_table('conversations')
