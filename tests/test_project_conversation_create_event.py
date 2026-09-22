"""Regression test: ``POST /projects/{id}/conversations`` must publish a
``conversation_list_changed`` per-user event, exactly like the standalone
``POST /conversations`` endpoint does.

Before the fix, a chat created inside a project (the home composer's first
send while the sidebar is drilled into that project) only showed up in the
sidebar once the first reply finished streaming or the 30s project poll
fired, because nothing nudged the drilled sidebar to refetch.

Calls the route function directly with an isolated DB and a captured bus.
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
    """Point the store modules at a throwaway sqlite file + data dirs
    (same no-reload pattern as test_convert_conversation_to_project.py)."""
    tmpdir = tempfile.mkdtemp(prefix="quest_project_conv_event_test_")
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
def _captured_bus(monkeypatch):
    from chat.project_routes import bus

    published: list[tuple[int, dict]] = []

    def _capture(user_id, event):
        published.append((user_id, event))

    monkeypatch.setattr(bus, "publish_to_user", _capture)
    return published


def test_create_project_conversation_publishes_list_changed(
    _isolated_storage, _captured_bus,
):
    storage_mod, conv_store_mod, project_store_mod, models_mod = _isolated_storage
    from chat.project_routes import (
        CreateProjectConversationRequest,
        create_project_conversation,
    )

    async def _seed():
        async with conv_store_mod.AsyncSessionLocal() as db:
            u = models_mod.User(
                email=f"proj-{uuid.uuid4().hex}@example.com",
                api_key=f"k-{uuid.uuid4().hex}",
                name="Project Tester",
            )
            db.add(u)
            await db.commit()
            await db.refresh(u)
            user = {"id": u.id, "email": u.email}
        project = await project_store_mod.create_project(user["id"], name="P")
        return user, project["id"]

    user, project_id = _run(_seed())

    response = _run(create_project_conversation(
        project_id, CreateProjectConversationRequest(), user,
    ))

    assert response["project_id"] == project_id
    events = [
        (uid, ev) for uid, ev in _captured_bus
        if ev.get("type") == "conversation_list_changed"
    ]
    assert events == [(
        user["id"],
        {
            "type": "conversation_list_changed",
            "conversation_id": response["id"],
            "action": "created",
        },
    )]
