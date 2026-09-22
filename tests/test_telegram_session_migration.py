"""End-to-end test of migration d7a1f3c9e2b4 (users.telegram_session ->
user_service_credentials rows for the Telegram plugin).

Builds a database at the encryption revision in a temp data dir, seeds
users whose ``telegram_session`` is encrypted under the old column label
(plus one legacy plaintext row, one without a session, and one that
already has a ``telegram`` credential row), upgrades to head, and checks:
the column is gone, each session landed as ``{"session": ...}`` in an
encrypted ``oauth_blob`` readable through the ORM store, the existing row
won, re-running is a no-op, and downgrade restores the column with the
value re-encrypted under the old label.

Alembic, the seeding, and the ORM read-back run in subprocesses
(``uv run``) so the env (QUEST_DATA_DIR, the key file, the password) is
exactly what a deployment would have, without disturbing this process's
engine or encryption cache.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from config import encryption
from config.paths import PROJECT_ROOT

ENCRYPTION_REV = "c4e8a2d17f63"
PASSWORD = "migration-test-password"


@pytest.fixture()
def deployment(tmp_path):
    """A temp data dir with its own key file and the env to address it."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    key_file = data_dir / "encryption_key.json"
    env = {
        **os.environ,
        "QUEST_ENV": "prod",
        "QUEST_DATA_DIR": str(data_dir),
        "QUEST_ENCRYPTION_KEY_FILE": str(key_file),
        "QUEST_ENCRYPTION_PASSWORD": PASSWORD,
    }
    env.pop("QUEST_ENCRYPTION_PASSWORD_FILE", None)
    encryption.create_key_file(PASSWORD, key_file)
    return data_dir, env


def _run(args: list[str], env: dict, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", *args], cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, check=check,
    )


def _alembic(env: dict, *args: str) -> subprocess.CompletedProcess:
    return _run(["alembic", *args], env)


def _python(env: dict, script: str) -> str:
    result = _run(["python", "-c", script], env)
    return result.stdout.strip().splitlines()[-1]


def _rows(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    return {r[1] for r in _rows(db_path, f"PRAGMA table_info({table})")}


_SEED = """
import json, sqlite3, os
from config import encryption
from config.paths import DATABASE_PATH
enc = lambda s: encryption.encrypt_str(s, "users.telegram_session")
blob = lambda d: json.dumps(encryption.encrypt_json(d, "user_service_credentials.oauth_blob"))
conn = sqlite3.connect(DATABASE_PATH)
ins = ("INSERT INTO users (id, email, name, api_key, settings, telegram_session, created_at) "
       "VALUES (?, ?, ?, ?, '{}', ?, CURRENT_TIMESTAMP)")
conn.execute(ins, (1, 'a@example.com', 'A', 'key-a', enc('tg-a')))
conn.execute(ins, (2, 'b@example.com', 'B', 'key-b', 'tg-b-plaintext'))
conn.execute(ins, (3, 'c@example.com', 'C', 'key-c', None))
conn.execute(ins, (4, 'd@example.com', 'D', 'key-d', enc('tg-d-stale')))
conn.execute(
    "INSERT INTO user_service_credentials (user_id, service, oauth_blob, created_at, updated_at) "
    "VALUES (4, 'telegram', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
    (blob({"session": "tg-d-existing"}),),
)
conn.execute(
    "INSERT INTO user_service_credentials (user_id, service, oauth_blob, created_at, updated_at) "
    "VALUES (1, 'slack', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
    (blob({"access_token": "xoxp-a"}),),
)
conn.commit(); conn.close()
print("seeded")
"""

_READ_ROWS = """
import asyncio, json
from db.user_service_credential_store import get_credential
out = {}
for uid in (1, 2, 3, 4):
    row = asyncio.run(get_credential(uid, "telegram"))
    out[str(uid)] = row and row["oauth_blob"]
out["slack"] = asyncio.run(get_credential(1, "slack"))["oauth_blob"]
print(json.dumps(out))
"""

_READ_COLUMN = """
import json, sqlite3
from config import encryption
from config.paths import DATABASE_PATH
conn = sqlite3.connect(DATABASE_PATH)
rows = conn.execute("SELECT id, telegram_session FROM users ORDER BY id").fetchall()
out = {}
for uid, raw in rows:
    if raw is None:
        out[str(uid)] = None
    else:
        assert encryption.is_encrypted(raw), raw
        out[str(uid)] = encryption.decrypt_str(raw, "users.telegram_session")
print(json.dumps(out))
"""


def test_sessions_move_into_credential_rows_and_back(deployment):
    data_dir, env = deployment
    db_path = data_dir / "quest.db"

    _alembic(env, "upgrade", ENCRYPTION_REV)
    assert _python(env, _SEED) == "seeded"

    _alembic(env, "upgrade", "head")

    assert "telegram_session" not in _columns(db_path, "users")
    # Everything in the table is ciphertext: no session string is readable.
    raw = " ".join(str(v) for row in _rows(db_path, "SELECT oauth_blob FROM user_service_credentials") for v in row)
    for secret in ("tg-a", "tg-b-plaintext", "tg-d-existing"):
        assert secret not in raw

    rows = json.loads(_python(env, _READ_ROWS))
    assert rows["1"] == {"session": "tg-a"}
    assert rows["2"] == {"session": "tg-b-plaintext"}  # legacy plaintext row accepted
    assert rows["3"] is None
    assert rows["4"] == {"session": "tg-d-existing"}  # existing row wins
    assert rows["slack"] == {"access_token": "xoxp-a"}  # untouched
    assert _rows(db_path, "SELECT COUNT(*) FROM user_service_credentials WHERE service = 'telegram'") == [(3,)]

    # Re-running at head is a no-op.
    _alembic(env, "upgrade", "head")
    assert json.loads(_python(env, _READ_ROWS)) == rows

    # Downgrade restores the column (re-encrypted under the old label) and
    # removes the rows.
    _alembic(env, "downgrade", ENCRYPTION_REV)
    assert "telegram_session" in _columns(db_path, "users")
    assert json.loads(_python(env, _READ_COLUMN)) == {
        "1": "tg-a", "2": "tg-b-plaintext", "3": None, "4": "tg-d-existing",
    }
    assert _rows(db_path, "SELECT COUNT(*) FROM user_service_credentials WHERE service = 'telegram'") == [(0,)]
    assert _rows(db_path, "SELECT COUNT(*) FROM user_service_credentials WHERE service = 'slack'") == [(1,)]


def test_upgrade_refuses_without_the_key(deployment):
    data_dir, env = deployment
    _alembic(env, "upgrade", ENCRYPTION_REV)
    bad_env = {**env, "QUEST_ENCRYPTION_PASSWORD": "wrong-password"}
    result = _run(["alembic", "upgrade", "head"], bad_env, check=False)
    assert result.returncode != 0
    assert "encryption-at-rest key" in (result.stderr + result.stdout)
    assert "telegram_session" in _columns(data_dir / "quest.db", "users")
