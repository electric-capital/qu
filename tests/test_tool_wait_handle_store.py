"""Tests for db.tool_wait_handle_store CRUD helpers.

Uses an isolated SQLite file so each test run starts with a fresh schema.
Async coroutines are driven via ``asyncio.run`` to match the existing
async-test convention in this repo.
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from importlib import reload

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def _isolated_db(monkeypatch):
    """Point engine + paths at a fresh sqlite file, then create the schema."""
    tmpdir = tempfile.mkdtemp(prefix="quest_wait_handle_test_")
    db_path = os.path.join(tmpdir, "quest.db")

    from config import paths
    monkeypatch.setattr(paths, "DATABASE_PATH", db_path, raising=True)

    import db.engine as engine_mod
    reload(engine_mod)
    import db.models as models_mod
    reload(models_mod)
    import db.tool_wait_handle_store as store_mod
    reload(store_mod)

    models_mod.Base.metadata.create_all(engine_mod.engine)

    yield store_mod, models_mod

    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture()
def _seed_user(_isolated_db):
    _store, models_mod = _isolated_db
    from db.engine import AsyncSessionLocal

    async def _create():
        async with AsyncSessionLocal() as db:
            u = models_mod.User(
                email=f"test-{uuid.uuid4().hex}@example.com",
                api_key=f"k-{uuid.uuid4().hex}",
                name="Test",
            )
            db.add(u)
            await db.commit()
            await db.refresh(u)
            return u.id

    return _run(_create())


def test_create_handle_populates_fields(_isolated_db, _seed_user):
    store, _models = _isolated_db
    handle = _run(store.create_handle(
        user_id=_seed_user,
        conversation_id="conv-1",
        kind="slack_reply",
        tool_id="slack_reply_abc",
        payload={"content": "Likes tea"},
    ))
    assert handle["id"]
    assert handle["status"] == "pending"
    assert handle["payload"] == {"content": "Likes tea"}
    assert handle["resolved_at"] is None
    fetched = _run(store.get_handle(handle["id"]))
    assert fetched is not None
    assert fetched["id"] == handle["id"]


def test_resolve_handle_flips_status(_isolated_db, _seed_user):
    store, _models = _isolated_db
    handle = _run(store.create_handle(
        user_id=_seed_user, conversation_id="conv-1",
        kind="slack_reply", tool_id="t1", payload={"content": "x"},
    ))
    updated = _run(store.resolve_handle(
        handle["id"], new_status="accepted",
        response={"memory_id": "m-1"},
        correlation_kind="memory", correlation_id="m-1",
    ))
    assert updated is not None
    assert updated["status"] == "accepted"
    assert updated["response"] == {"memory_id": "m-1"}
    assert updated["resolved_at"] is not None

    again = _run(store.resolve_handle(handle["id"], new_status="rejected"))
    assert again is None


def test_bulk_get_filters_by_user(_isolated_db, _seed_user):
    store, models_mod = _isolated_db
    from db.engine import AsyncSessionLocal

    async def _create_other():
        async with AsyncSessionLocal() as db:
            u2 = models_mod.User(
                email=f"other-{uuid.uuid4().hex}@example.com",
                api_key=f"k-{uuid.uuid4().hex}",
                name="Other",
            )
            db.add(u2)
            await db.commit()
            await db.refresh(u2)
            return u2.id

    other_user_id = _run(_create_other())

    h1 = _run(store.create_handle(
        user_id=_seed_user, conversation_id="c1",
        kind="slack_reply", tool_id="t1", payload={},
    ))
    h2 = _run(store.create_handle(
        user_id=other_user_id, conversation_id="c2",
        kind="slack_reply", tool_id="t2", payload={},
    ))
    rows = _run(store.bulk_get_handles_by_ids(
        _seed_user, [h1["id"], h2["id"]],
    ))
    ids = {r["id"] for r in rows}
    assert h1["id"] in ids
    assert h2["id"] not in ids


def test_cancel_pending_for_conversation(_isolated_db, _seed_user):
    store, _models = _isolated_db
    h1 = _run(store.create_handle(
        user_id=_seed_user, conversation_id="c1",
        kind="slack_reply", tool_id="t1", payload={},
    ))
    h2 = _run(store.create_handle(
        user_id=_seed_user, conversation_id="c1",
        kind="slack_reply", tool_id="t2", payload={},
    ))
    h3 = _run(store.create_handle(
        user_id=_seed_user, conversation_id="c-other",
        kind="slack_reply", tool_id="t3", payload={},
    ))
    cancelled = _run(store.cancel_pending_for_conversation(_seed_user, "c1"))
    cancelled_ids = {r["id"] for r in cancelled}
    assert cancelled_ids == {h1["id"], h2["id"]}
    untouched = _run(store.get_handle(h3["id"]))
    assert untouched["status"] == "pending"


def test_mark_timed_out_skips_resolved(_isolated_db, _seed_user):
    store, _models = _isolated_db
    h1 = _run(store.create_handle(
        user_id=_seed_user, conversation_id="c1",
        kind="slack_reply", tool_id="t1", payload={},
    ))
    h2 = _run(store.create_handle(
        user_id=_seed_user, conversation_id="c1",
        kind="slack_reply", tool_id="t2", payload={},
    ))
    _run(store.resolve_handle(h2["id"], new_status="accepted"))
    rows = _run(store.mark_timed_out([h1["id"], h2["id"]], _seed_user))
    timed_out_ids = {r["id"] for r in rows}
    assert h1["id"] in timed_out_ids
    assert h2["id"] not in timed_out_ids


def test_get_handle_by_tool_id_scopes_user(_isolated_db, _seed_user):
    store, _models = _isolated_db
    h = _run(store.create_handle(
        user_id=_seed_user, conversation_id="c1",
        kind="slack_reply", tool_id="slack_reply_xy", payload={},
    ))
    found = _run(store.get_handle_by_tool_id(_seed_user, "c1", "slack_reply_xy"))
    assert found is not None and found["id"] == h["id"]
    not_found = _run(store.get_handle_by_tool_id(
        _seed_user, "c1", "slack_reply_other",
    ))
    assert not_found is None
