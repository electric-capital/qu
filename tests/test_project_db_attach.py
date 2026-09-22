"""Regression tests for the project_db_query ATTACH guard (finding #279229).

The textual ``startswith("ATTACH")`` check is only a fast path.  A leading SQL
comment bypassed it, letting the model create or open SQLite files at any path
the service process can reach (e.g. ``frontend/dist``, where the
unauthenticated ``/{filename}`` static fallback then reveals which files exist).
The real guard is a SQLite authorizer that denies SQLITE_ATTACH at
statement-compile time.
"""

import asyncio
import json

import pytest

from chat.gemini_api.tool_handlers.project_db import (
    _ATTACH_BLOCKED_ERROR,
    _execute_project_db_query,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "project.db"


def _run(db_path, query):
    return json.loads(asyncio.run(_execute_project_db_query(db_path, query)))


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "/* injected */ ",
        "-- injected\n",
        "\n\t/* a */ /* b */\n",
        "; ",
    ],
    ids=["plain", "block-comment", "line-comment", "mixed-whitespace", "empty-stmt"],
)
@pytest.mark.parametrize("keyword", ["ATTACH DATABASE", "attach database", "ATTACH"])
def test_attach_is_rejected_regardless_of_prefix(db_path, tmp_path, prefix, keyword):
    target = tmp_path / "marker.sqlite"
    result = _run(db_path, f"{prefix}{keyword} '{target}' AS leak")
    assert result == {"error": _ATTACH_BLOCKED_ERROR}
    assert not target.exists(), "ATTACH must not create a file outside the project database"


def test_attach_cannot_open_existing_sqlite_file(db_path, tmp_path):
    import sqlite3

    other = tmp_path / "other_project.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE secrets (v TEXT)")
    conn.execute("INSERT INTO secrets VALUES ('victim')")
    conn.commit()
    conn.close()

    result = _run(db_path, f"/* c */ ATTACH '{other}' AS x")
    assert result == {"error": _ATTACH_BLOCKED_ERROR}


def test_normal_queries_still_work_with_authorizer(db_path):
    assert (_run(db_path, "CREATE TABLE t (x TEXT)"))["status"] == "ok"
    assert (_run(db_path, "/* comment */ INSERT INTO t VALUES ('a')"))["rows_affected"] == 1
    assert (_run(db_path, "SELECT x FROM t"))["rows"] == [["a"]]
    assert (_run(db_path, "PRAGMA table_info(t)"))["row_count"] == 1
    assert (_run(db_path, "EXPLAIN SELECT 1"))["row_count"] >= 1
    assert (_run(db_path, "DROP TABLE t"))["status"] == "ok"


def test_other_sql_errors_are_still_reported_verbatim(db_path):
    result = _run(db_path, "SELECT * FROM does_not_exist")
    assert result["error"].startswith("SQL error: no such table")
