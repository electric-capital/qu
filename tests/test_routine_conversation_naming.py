"""Routine conversations are named after their routine at creation time.

Every run of a routine starts from the same prompt, so asking the model to
call ``set_conversation_name`` on each run only burned a tool call for a
name that was the same each time. Instead ``ChatStorage.create_project_conversation``
copies the routine's name into ``custom_name`` when a ``routine_id`` is given
(the sidebar play button and the scheduler both go through it), and
``get_system_prompt(is_routine=True)`` drops the first-reply naming
instruction along with the ``set_conversation_name`` tool.

Uses the same isolated-DB pattern as test_project_conversation_routine_scope.py.
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def _isolated_storage(monkeypatch):
    tmpdir = tempfile.mkdtemp(prefix="quest_routine_naming_test_")
    db_path = os.path.join(tmpdir, "quest.db")
    chats_dir = os.path.join(tmpdir, "chats")
    projects_dir = os.path.join(tmpdir, "projects")
    os.makedirs(chats_dir, exist_ok=True)
    os.makedirs(projects_dir, exist_ok=True)

    sync_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False},
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    test_session_local = async_sessionmaker(async_engine, expire_on_commit=False)

    import db.models as models_mod
    import db.conversation_store as conv_store_mod
    import db.project_store as project_store_mod
    import db.routine_store as routine_store_mod
    import chat.storage as storage_mod

    models_mod.Base.metadata.create_all(sync_engine)

    for mod in (conv_store_mod, project_store_mod, routine_store_mod):
        monkeypatch.setattr(mod, "AsyncSessionLocal", test_session_local)
    monkeypatch.setattr(storage_mod, "CHATS_DIR", Path(chats_dir), raising=True)
    monkeypatch.setattr(storage_mod, "PROJECTS_DIR", Path(projects_dir), raising=True)

    from chat.project_routes import bus
    monkeypatch.setattr(bus, "publish_to_user", lambda *_a, **_k: None)

    yield {
        "conv": conv_store_mod,
        "project": project_store_mod,
        "routine": routine_store_mod,
        "models": models_mod,
        "storage": storage_mod,
    }

    _run(async_engine.dispose())
    sync_engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def _seed(mods, routine_name="Daily digest"):
    async with mods["conv"].AsyncSessionLocal() as db:
        u = mods["models"].User(
            email=f"owner-{uuid.uuid4().hex}@example.com",
            api_key=f"k-{uuid.uuid4().hex}",
            name="Owner",
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        user = {"id": u.id, "email": u.email}
    project = await mods["project"].create_project(user["id"], name="P")
    routine = await mods["routine"].create_routine(
        user["id"], project["id"], name=routine_name, prompt="summarize inbox",
    )
    return user, project, routine


def test_storage_names_routine_conversation_after_routine(_isolated_storage):
    """The scheduler path: ChatStorage.create_project_conversation directly."""
    mods = _isolated_storage
    user, project, routine = _run(_seed(mods))

    conv_id, _created = _run(mods["storage"].ChatStorage.create_project_conversation(
        user["id"], project["id"], routine_id=routine["id"],
    ))

    meta = _run(mods["conv"].get_conversation_meta(user["id"], conv_id))
    assert meta["routine_id"] == routine["id"]
    assert meta["custom_name"] == "Daily digest"
    assert mods["storage"].ChatStorage._resolve_list_title(conv_id, meta) == "Daily digest"


def test_route_names_routine_conversation_after_routine(_isolated_storage):
    """The sidebar play-button path: POST /projects/{id}/conversations."""
    mods = _isolated_storage
    user, project, routine = _run(_seed(mods, routine_name="Weekly report"))

    from chat.project_routes import (
        CreateProjectConversationRequest,
        create_project_conversation,
    )
    response = _run(create_project_conversation(
        project["id"], CreateProjectConversationRequest(routine_id=routine["id"]), user,
    ))

    meta = _run(mods["conv"].get_conversation_meta(user["id"], response["id"]))
    assert meta["custom_name"] == "Weekly report"


def test_plain_project_conversation_is_not_named(_isolated_storage):
    """Without a routine the auto-title path stays in charge."""
    mods = _isolated_storage
    user, project, _routine = _run(_seed(mods))

    conv_id, _created = _run(mods["storage"].ChatStorage.create_project_conversation(
        user["id"], project["id"],
    ))

    meta = _run(mods["conv"].get_conversation_meta(user["id"], conv_id))
    assert meta["routine_id"] is None
    assert meta["custom_name"] is None


def test_set_conversation_name_is_ignored_on_routine_conversation(_isolated_storage):
    """A cached model that still calls the tool cannot overwrite the routine name."""
    mods = _isolated_storage
    user, project, routine = _run(_seed(mods))
    conv_id, _created = _run(mods["storage"].ChatStorage.create_project_conversation(
        user["id"], project["id"], routine_id=routine["id"],
    ))

    import json
    from chat.gemini_api.tool_handlers.misc import _handle_set_conversation_name
    result = json.loads(_run(_handle_set_conversation_name(
        user["id"], conv_id, "Something else",
    )))

    assert result.get("name_set") is False
    meta = _run(mods["conv"].get_conversation_meta(user["id"], conv_id))
    assert meta["custom_name"] == "Daily digest"


def test_db_create_truncates_custom_name(_isolated_storage):
    from datetime import datetime, timezone
    mods = _isolated_storage
    user, project, _routine = _run(_seed(mods))
    from db.conversation_store import MAX_CONVERSATION_NAME_LENGTH

    row = _run(mods["conv"].create_conversation(
        user["id"], str(uuid.uuid4()), datetime.now(timezone.utc),
        project_id=project["id"], custom_name="  " + "x" * 150 + "  ",
    ))
    assert row["custom_name"] == "x" * MAX_CONVERSATION_NAME_LENGTH

    blank = _run(mods["conv"].create_conversation(
        user["id"], str(uuid.uuid4()), datetime.now(timezone.utc),
        project_id=project["id"], custom_name="   ",
    ))
    assert blank["custom_name"] is None


# --- System prompt -----------------------------------------------------------


def test_regular_prompt_keeps_naming_instruction_and_tool():
    from chat.gemini_api.system_prompt import get_system_prompt
    prompt = get_system_prompt("key", has_project=True)
    assert "Conversation naming (IMPORTANT -- do this on every first reply)" in prompt
    assert 'tool_call(tool_name="set_conversation_name", arguments={"name": "<short summary>"})' in prompt
    assert "**set_conversation_name**" in prompt or "set_conversation_name(" in prompt


def test_routine_prompt_drops_naming_instruction_and_tool():
    from chat.gemini_api.system_prompt import get_system_prompt
    prompt = get_system_prompt("key", has_project=True, is_routine=True)
    assert "do this on every first reply" not in prompt
    assert "already named after its routine" in prompt
    assert "set_conversation_name" not in prompt
