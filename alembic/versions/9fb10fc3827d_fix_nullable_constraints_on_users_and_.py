"""fix_nullable_constraints_on_users_and_memories

Revision ID: 9fb10fc3827d
Revises: 498112eb3e5d
Create Date: 2026-02-23 23:14:54.500422

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9fb10fc3827d'
down_revision: Union[str, Sequence[str], None] = '498112eb3e5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Fix NOT NULL constraints lost during PK migration (a1b2c3d4e5f6).

    The raw SQL CREATE TABLE in that migration omitted NOT NULL on several
    columns that the SQLAlchemy models declare as non-nullable.  This uses
    batch_alter_table which rebuilds the table on SQLite.
    """
    conn = op.get_bind()

    # Safety: fill any NULL values with defaults before applying NOT NULL
    conn.execute(sa.text(
        "UPDATE users SET name = '' WHERE name IS NULL"
    ))
    conn.execute(sa.text(
        "UPDATE users SET api_key = '' WHERE api_key IS NULL"
    ))
    conn.execute(sa.text(
        "UPDATE users SET created_at = datetime('now') WHERE created_at IS NULL"
    ))
    conn.execute(sa.text(
        "UPDATE memories SET created_at = datetime('now') WHERE created_at IS NULL"
    ))

    # Fix users table: name, api_key, created_at should be NOT NULL
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('name',
                              existing_type=sa.VARCHAR(length=255),
                              nullable=False,
                              existing_server_default=sa.text("('')"))
        batch_op.alter_column('api_key',
                              existing_type=sa.VARCHAR(length=64),
                              nullable=False)
        batch_op.alter_column('created_at',
                              existing_type=sa.DATETIME(),
                              nullable=False)

    # Fix memories table: created_at should be NOT NULL
    # Note: batch_alter_table on SQLite rebuilds the table, which may drop
    # triggers. We check and re-create FTS5 triggers if needed.
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.alter_column('created_at',
                              existing_type=sa.DATETIME(),
                              nullable=False)

    # Verify FTS5 triggers still exist after table rebuild
    triggers = conn.execute(sa.text(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='memories'"
    )).fetchall()
    trigger_names = {row[0] for row in triggers}
    expected = {'memories_ai', 'memories_ad', 'memories_au'}
    missing = expected - trigger_names

    if missing:
        # Re-create missing FTS5 triggers (copied from migration 94c49d92fea7)
        if 'memories_ai' in missing:
            op.execute("""
                CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content)
                    VALUES (NEW.rowid, NEW.content);
                END;
            """)
        if 'memories_ad' in missing:
            op.execute("""
                CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES ('delete', OLD.rowid, OLD.content);
                END;
            """)
        if 'memories_au' in missing:
            op.execute("""
                CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES ('delete', OLD.rowid, OLD.content);
                    INSERT INTO memories_fts(rowid, content)
                    VALUES (NEW.rowid, NEW.content);
                END;
            """)


def downgrade() -> None:
    """Revert NOT NULL constraints."""
    conn = op.get_bind()

    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.alter_column('created_at',
                              existing_type=sa.DATETIME(),
                              nullable=True)

    # Verify FTS5 triggers still exist after table rebuild
    triggers = conn.execute(sa.text(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='memories'"
    )).fetchall()
    trigger_names = {row[0] for row in triggers}
    expected = {'memories_ai', 'memories_ad', 'memories_au'}
    missing = expected - trigger_names

    if missing:
        if 'memories_ai' in missing:
            op.execute("""
                CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content)
                    VALUES (NEW.rowid, NEW.content);
                END;
            """)
        if 'memories_ad' in missing:
            op.execute("""
                CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES ('delete', OLD.rowid, OLD.content);
                END;
            """)
        if 'memories_au' in missing:
            op.execute("""
                CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES ('delete', OLD.rowid, OLD.content);
                    INSERT INTO memories_fts(rowid, content)
                    VALUES (NEW.rowid, NEW.content);
                END;
            """)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('created_at',
                              existing_type=sa.DATETIME(),
                              nullable=True)
        batch_op.alter_column('api_key',
                              existing_type=sa.VARCHAR(length=64),
                              nullable=True)
        batch_op.alter_column('name',
                              existing_type=sa.VARCHAR(length=255),
                              nullable=True,
                              existing_server_default=sa.text("('')"))
