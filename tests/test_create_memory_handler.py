"""Tests for the create_memory action_request handler.

Covers ``CreateMemoryHandler.validate_params`` (empty / whitespace /
4 KB cap) and ``CreateMemoryHandler.execute`` (creates a row, returns
``memory_id``).
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
    tmpdir = tempfile.mkdtemp(prefix="quest_create_memory_test_")
    db_path = os.path.join(tmpdir, "quest.db")

    from config import paths
    monkeypatch.setattr(paths, "DATABASE_PATH", db_path, raising=True)

    import db.engine as engine_mod
    reload(engine_mod)
    import db.models as models_mod
    reload(models_mod)
    import db.memory_store as memory_store_mod
    reload(memory_store_mod)

    models_mod.Base.metadata.create_all(engine_mod.engine)

    yield memory_store_mod, models_mod

    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture()
def _user_id(_isolated_db):
    _store, models_mod = _isolated_db
    from db.engine import AsyncSessionLocal

    async def _create():
        async with AsyncSessionLocal() as db:
            u = models_mod.User(
                email=f"cmh-{uuid.uuid4().hex}@example.com",
                api_key=f"k-{uuid.uuid4().hex}",
                name="Memory Tester",
            )
            db.add(u)
            await db.commit()
            await db.refresh(u)
            return u.id

    return _run(_create())


def _handler():
    from chat.action_request_types.create_memory import CreateMemoryHandler
    return CreateMemoryHandler()


def test_validate_params_strips_whitespace():
    handler = _handler()
    out = handler.validate_params({"content": "   Prefers tea   "})
    assert out == {"content": "Prefers tea"}


def test_validate_params_rejects_missing_content():
    handler = _handler()
    with pytest.raises(ValueError, match="content"):
        handler.validate_params({})


def test_validate_params_rejects_empty_content():
    handler = _handler()
    with pytest.raises(ValueError, match="empty"):
        handler.validate_params({"content": ""})


def test_validate_params_rejects_whitespace_only_content():
    handler = _handler()
    with pytest.raises(ValueError, match="empty"):
        handler.validate_params({"content": "   \n\t  "})


def test_validate_params_rejects_oversize_content():
    from db.memory_store import MAX_MEMORY_SIZE

    handler = _handler()
    too_big = "x" * (MAX_MEMORY_SIZE + 1)
    with pytest.raises(ValueError, match="maximum size"):
        handler.validate_params({"content": too_big})


def test_validate_params_accepts_at_cap():
    from db.memory_store import MAX_MEMORY_SIZE

    handler = _handler()
    at_cap = "x" * MAX_MEMORY_SIZE
    out = handler.validate_params({"content": at_cap})
    assert out["content"] == at_cap


def test_validate_params_coerces_non_string_to_string():
    handler = _handler()
    out = handler.validate_params({"content": 42})
    assert out == {"content": "42"}


def test_render_preview_returns_memory_field():
    handler = _handler()

    async def _go():
        return await handler.render_preview({"content": "Likes coffee"})

    out = _run(_go())
    assert out == [{"key": "Memory", "value": "Likes coffee"}]


def test_render_preview_empty_content_returns_empty_list():
    handler = _handler()

    async def _go():
        return await handler.render_preview({"content": ""})

    out = _run(_go())
    assert out == []


def test_handler_metadata():
    handler = _handler()
    from db.models import ActionRequestType

    assert handler.type_name == ActionRequestType.CREATE_MEMORY
    assert handler.display_name == "Save Memory"
    assert handler.approve_label == "Save"


def test_execute_creates_memory_row(_isolated_db, _user_id):
    memory_store, _ = _isolated_db
    handler = _handler()

    async def _go():
        result = await handler.execute(
            {"content": "Prefers window seats on flights"},
            {"id": _user_id},
        )
        return result

    result = _run(_go())
    assert result["success"] is True
    assert "memory_id" in result and result["memory_id"]

    # Confirm the memory persisted with the expected content.
    persisted = _run(memory_store.get_memory(_user_id, result["memory_id"]))
    assert persisted is not None
    assert persisted["content"] == "Prefers window seats on flights"
    assert persisted["user_id"] == _user_id
