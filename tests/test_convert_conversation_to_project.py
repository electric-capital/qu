"""Tests for converting a standalone conversation into a project.

Covers the two building blocks behind ``POST /projects/from-conversation``:

* ``ChatStorage.move_conversation_workspace_to_project`` -- moves the files
  under ``data/chats/{id}/workspace/`` into the project's shared
  ``data/projects/{pid}/workspace/workspace/`` while leaving conversation
  metadata files (chat_history.json, ...) behind.
* ``conversation_store.set_conversation_project`` -- attaches a standalone
  conversation to a project, refusing wrong-owner and already-in-project rows.
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
    """Point the store modules at a throwaway sqlite file + data dirs.

    Deliberately avoids importlib.reload(): reloading replaces class/function
    objects inside the shared modules, which breaks ``patch("chat.storage...")``
    targets for unrelated tests that run later in the same pytest session.
    Instead, swap the session factory and path constants on the already-loaded
    modules -- monkeypatch restores the originals on teardown.
    """
    tmpdir = tempfile.mkdtemp(prefix="quest_convert_project_test_")
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
    import chat.storage as storage_mod

    models_mod.Base.metadata.create_all(sync_engine)

    monkeypatch.setattr(conv_store_mod, "AsyncSessionLocal", test_session_local)
    monkeypatch.setattr(project_store_mod, "AsyncSessionLocal", test_session_local)
    monkeypatch.setattr(storage_mod, "CHATS_DIR", Path(chats_dir), raising=True)
    monkeypatch.setattr(storage_mod, "PROJECTS_DIR", Path(projects_dir), raising=True)

    yield storage_mod, conv_store_mod, project_store_mod, models_mod

    _run(async_engine.dispose())
    sync_engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture()
def _seed(_isolated_storage):
    """Create a user, a standalone conversation, and a project row."""
    storage_mod, conv_store_mod, project_store_mod, models_mod = _isolated_storage

    async def _create():
        # conv_store_mod.AsyncSessionLocal is the patched test factory.
        async with conv_store_mod.AsyncSessionLocal() as db:
            u = models_mod.User(
                email=f"conv-{uuid.uuid4().hex}@example.com",
                api_key=f"k-{uuid.uuid4().hex}",
                name="Convert Tester",
            )
            db.add(u)
            await db.commit()
            await db.refresh(u)
            user_id = u.id

        conversation_id, _ = await storage_mod.ChatStorage.create_conversation(user_id)
        project = await project_store_mod.create_project(user_id, name="Converted")
        return user_id, conversation_id, project["id"]

    return _run(_create())


def test_move_workspace_files_into_project(_isolated_storage, _seed):
    storage_mod, conv_store_mod, _project_store_mod, _ = _isolated_storage
    user_id, conversation_id, project_id = _seed
    ChatStorage = storage_mod.ChatStorage

    # Seed workspace files, including a nested directory.
    conv_dir = ChatStorage._get_conversation_dir(conversation_id)
    ws = conv_dir / "workspace"
    (ws / "sub").mkdir(parents=True)
    (ws / "notes.md").write_text("hello")
    (ws / "sub" / "data.csv").write_text("a,b\n1,2")

    ChatStorage.create_project_workspace(project_id)
    ChatStorage.move_conversation_workspace_to_project(conversation_id, project_id)

    dest = storage_mod.PROJECTS_DIR / project_id / "workspace" / "workspace"
    assert (dest / "notes.md").read_text() == "hello"
    assert (dest / "sub" / "data.csv").read_text() == "a,b\n1,2"

    # Source workspace is gone; conversation metadata stays behind.
    assert not ws.exists()
    assert (conv_dir / "chat_history.json").exists()


def test_move_workspace_noop_without_workspace_dir(_isolated_storage, _seed):
    storage_mod, _conv_store_mod, _project_store_mod, _ = _isolated_storage
    _user_id, conversation_id, project_id = _seed
    ChatStorage = storage_mod.ChatStorage

    # No workspace/ subdir exists for a fresh conversation -- must not raise.
    ChatStorage.move_conversation_workspace_to_project(conversation_id, project_id)


def test_set_conversation_project_and_workspace_resolution(_isolated_storage, _seed):
    storage_mod, conv_store_mod, _project_store_mod, _ = _isolated_storage
    user_id, conversation_id, project_id = _seed
    ChatStorage = storage_mod.ChatStorage

    updated = _run(conv_store_mod.set_conversation_project(
        user_id, conversation_id, project_id,
    ))
    assert updated is not None
    assert updated["project_id"] == project_id

    meta = _run(conv_store_mod.get_conversation_meta(user_id, conversation_id))
    assert meta["project_id"] == project_id

    # Workspace resolution now points at the project's shared workspace.
    ws_path = _run(ChatStorage.get_workspace_path(conversation_id))
    assert ws_path == storage_mod.PROJECTS_DIR / project_id / "workspace"


def test_set_conversation_project_refuses_wrong_owner_and_reattach(_isolated_storage, _seed):
    storage_mod, conv_store_mod, project_store_mod, _ = _isolated_storage
    user_id, conversation_id, project_id = _seed

    # Wrong owner -> None, row untouched.
    assert _run(conv_store_mod.set_conversation_project(
        user_id + 999, conversation_id, project_id,
    )) is None
    meta = _run(conv_store_mod.get_conversation_meta(user_id, conversation_id))
    assert meta["project_id"] is None

    # Attach once, then a second attach (even to another project) is refused.
    assert _run(conv_store_mod.set_conversation_project(
        user_id, conversation_id, project_id,
    )) is not None
    other = _run(project_store_mod.create_project(user_id, name="Other"))
    assert _run(conv_store_mod.set_conversation_project(
        user_id, conversation_id, other["id"],
    )) is None
    meta = _run(conv_store_mod.get_conversation_meta(user_id, conversation_id))
    assert meta["project_id"] == project_id
