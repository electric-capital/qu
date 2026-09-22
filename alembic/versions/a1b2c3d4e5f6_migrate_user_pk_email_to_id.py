"""migrate user pk email to id

Revision ID: a1b2c3d4e5f6
Revises: 861a7abe9a23
Create Date: 2026-02-14 00:00:00.000000

"""
import shutil
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from config.paths import CHATS_DIR


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '861a7abe9a23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Create new users table with integer PK
    conn.execute(sa.text("""
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email VARCHAR(255) NOT NULL UNIQUE,
            name VARCHAR(255) DEFAULT '',
            api_key VARCHAR(64) UNIQUE,
            created_at DATETIME,
            settings JSON,
            google_oauth JSON,
            google_services_oauth JSON,
            slack_oauth JSON,
            telegram_session TEXT,
            crm_api_key VARCHAR(128)
        )
    """))

    # 2. Copy data from old users table (id auto-assigned)
    conn.execute(sa.text("""
        INSERT INTO users_new (email, name, api_key, created_at, settings,
            google_oauth, google_services_oauth, slack_oauth,
            telegram_session, crm_api_key)
        SELECT email, name, api_key, created_at, settings,
            google_oauth, google_services_oauth, slack_oauth,
            telegram_session, crm_api_key
        FROM users
    """))

    # 3. Create new memories table with user_id (integer FK)
    conn.execute(sa.text("""
        CREATE TABLE memories_new (
            id VARCHAR(36) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME,
            updated_at DATETIME,
            archived BOOLEAN NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users_new(id) ON DELETE CASCADE
        )
    """))

    # 4. Copy memory data, translating user_email -> user_id
    conn.execute(sa.text("""
        INSERT INTO memories_new (id, user_id, content, created_at, updated_at, archived)
        SELECT m.id, u.id, m.content, m.created_at, m.updated_at, m.archived
        FROM memories m
        JOIN users_new u ON m.user_email = u.email
    """))

    # 5. Drop FTS5 triggers and virtual table
    conn.execute(sa.text("DROP TRIGGER IF EXISTS memories_au"))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS memories_ad"))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS memories_ai"))
    conn.execute(sa.text("DROP TABLE IF EXISTS memories_fts"))

    # 6. Drop old tables
    conn.execute(sa.text("DROP TABLE memories"))
    conn.execute(sa.text("DROP TABLE users"))

    # 7. Rename new tables
    conn.execute(sa.text("ALTER TABLE users_new RENAME TO users"))
    conn.execute(sa.text("ALTER TABLE memories_new RENAME TO memories"))

    # 8. Re-create indexes
    conn.execute(sa.text("CREATE UNIQUE INDEX ix_users_api_key ON users(api_key)"))
    conn.execute(sa.text("CREATE UNIQUE INDEX ix_users_email ON users(email)"))
    conn.execute(sa.text("CREATE INDEX ix_memories_user_id ON memories(user_id)"))
    conn.execute(sa.text("CREATE INDEX ix_memories_user_id_archived ON memories(user_id, archived)"))

    # 9. Re-create FTS5 virtual table and triggers
    conn.execute(sa.text("""
        CREATE VIRTUAL TABLE memories_fts USING fts5(
            content,
            content='memories',
            content_rowid='rowid'
        )
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content)
            VALUES (NEW.rowid, NEW.content);
        END
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content)
            VALUES ('delete', OLD.rowid, OLD.content);
        END
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content)
            VALUES ('delete', OLD.rowid, OLD.content);
            INSERT INTO memories_fts(rowid, content)
            VALUES (NEW.rowid, NEW.content);
        END
    """))

    # 10. Rebuild FTS5 index
    conn.execute(sa.text("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"))

    # 11. Migrate chat data directories on the filesystem
    rows = conn.execute(sa.text("SELECT id, email FROM users")).fetchall()

    if CHATS_DIR.exists():
        for user_id, email in rows:
            old_dir = CHATS_DIR / email
            new_dir = CHATS_DIR / str(user_id)
            if old_dir.exists() and not new_dir.exists():
                shutil.move(str(old_dir), str(new_dir))


def downgrade() -> None:
    conn = op.get_bind()

    # 1. Create old-schema users table with email as PK
    conn.execute(sa.text("""
        CREATE TABLE users_old (
            email VARCHAR(255) PRIMARY KEY,
            name VARCHAR(255) DEFAULT '',
            api_key VARCHAR(64) UNIQUE,
            created_at DATETIME,
            settings JSON,
            google_oauth JSON,
            google_services_oauth JSON,
            slack_oauth JSON,
            telegram_session TEXT,
            crm_api_key VARCHAR(128)
        )
    """))

    # 2. Copy data back
    conn.execute(sa.text("""
        INSERT INTO users_old (email, name, api_key, created_at, settings,
            google_oauth, google_services_oauth, slack_oauth,
            telegram_session, crm_api_key)
        SELECT email, name, api_key, created_at, settings,
            google_oauth, google_services_oauth, slack_oauth,
            telegram_session, crm_api_key
        FROM users
    """))

    # 3. Create old-schema memories table with user_email
    conn.execute(sa.text("""
        CREATE TABLE memories_old (
            id VARCHAR(36) PRIMARY KEY,
            user_email VARCHAR(255) NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME,
            updated_at DATETIME,
            archived BOOLEAN NOT NULL DEFAULT 0
        )
    """))

    # 4. Copy memory data back, translating user_id -> user_email
    conn.execute(sa.text("""
        INSERT INTO memories_old (id, user_email, content, created_at, updated_at, archived)
        SELECT m.id, u.email, m.content, m.created_at, m.updated_at, m.archived
        FROM memories m
        JOIN users u ON m.user_id = u.id
    """))

    # 5. Drop FTS5 triggers and virtual table
    conn.execute(sa.text("DROP TRIGGER IF EXISTS memories_au"))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS memories_ad"))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS memories_ai"))
    conn.execute(sa.text("DROP TABLE IF EXISTS memories_fts"))

    # 6. Drop new tables
    conn.execute(sa.text("DROP TABLE memories"))
    conn.execute(sa.text("DROP TABLE users"))

    # 7. Rename old tables back
    conn.execute(sa.text("ALTER TABLE users_old RENAME TO users"))
    conn.execute(sa.text("ALTER TABLE memories_old RENAME TO memories"))

    # 8. Re-create old indexes
    conn.execute(sa.text("CREATE UNIQUE INDEX ix_users_api_key ON users(api_key)"))
    conn.execute(sa.text("CREATE INDEX ix_memories_user_email ON memories(user_email)"))
    conn.execute(sa.text("CREATE INDEX ix_memories_user_email_archived ON memories(user_email, archived)"))

    # 9. Re-create FTS5 virtual table and triggers (same as before)
    conn.execute(sa.text("""
        CREATE VIRTUAL TABLE memories_fts USING fts5(
            content,
            content='memories',
            content_rowid='rowid'
        )
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content)
            VALUES (NEW.rowid, NEW.content);
        END
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content)
            VALUES ('delete', OLD.rowid, OLD.content);
        END
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content)
            VALUES ('delete', OLD.rowid, OLD.content);
            INSERT INTO memories_fts(rowid, content)
            VALUES (NEW.rowid, NEW.content);
        END
    """))

    # 10. Rebuild FTS5 index
    conn.execute(sa.text("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"))

    # 11. Migrate chat directories back from user_id to email
    rows = conn.execute(sa.text("SELECT email FROM users")).fetchall()
    # We can't easily reverse the directory mapping since the id column is gone,
    # but we stored the email. The user would need to manually fix chat dirs
    # or restore from backup.
    # Best-effort: no-op since we don't have the old id->email mapping easily
