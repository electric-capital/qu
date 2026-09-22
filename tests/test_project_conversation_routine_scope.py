"""Regression tests for security finding #279147: a project conversation's
``routine_id`` must be one of the caller's own routines in that project.

``POST /projects/{id}/conversations`` verified the project but stored any
``routine_id`` from the body verbatim. The stored id then drove
``get_routine_autoloaded_skills`` on every turn, which joined
``routine_skill_autoloads`` to ``skills`` by routine id alone -- so a
foreign routine id pulled another user's private auto-loaded skill bodies
into the caller's system prompt.

Two layers are covered here: the endpoint rejects foreign / cross-project
routine ids with the routine routes' 404 shape, and the resolver itself is
scoped to the conversation owner + project as defense in depth.

Calls the route function directly with an isolated DB (same no-reload
pattern as test_project_conversation_create_event.py).
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def _isolated_storage(monkeypatch):
    tmpdir = tempfile.mkdtemp(prefix="quest_routine_scope_test_")
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
    import db.skill_store as skill_store_mod
    import chat.storage as storage_mod

    models_mod.Base.metadata.create_all(sync_engine)

    for mod in (conv_store_mod, project_store_mod, routine_store_mod, skill_store_mod):
        monkeypatch.setattr(mod, "AsyncSessionLocal", test_session_local)
    monkeypatch.setattr(storage_mod, "CHATS_DIR", Path(chats_dir), raising=True)
    monkeypatch.setattr(storage_mod, "PROJECTS_DIR", Path(projects_dir), raising=True)

    from chat.project_routes import bus
    monkeypatch.setattr(bus, "publish_to_user", lambda *_a, **_k: None)

    yield {
        "conv": conv_store_mod,
        "project": project_store_mod,
        "routine": routine_store_mod,
        "skill": skill_store_mod,
        "models": models_mod,
    }

    _run(async_engine.dispose())
    sync_engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def _make_user(mods, label: str) -> dict:
    async with mods["conv"].AsyncSessionLocal() as db:
        u = mods["models"].User(
            email=f"{label}-{uuid.uuid4().hex}@example.com",
            api_key=f"k-{uuid.uuid4().hex}",
            name=label,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return {"id": u.id, "email": u.email}


async def _seed(mods):
    """Two users. The victim owns a project with a routine that auto-loads
    one of their private skills; the attacker owns their own project."""
    victim = await _make_user(mods, "victim")
    attacker = await _make_user(mods, "attacker")

    victim_project = await mods["project"].create_project(victim["id"], name="Victim P")
    victim_routine = await mods["routine"].create_routine(
        victim["id"], victim_project["id"], name="Secret routine", prompt="go",
    )
    secret_skill = await mods["skill"].create_skill(
        victim["id"], name="Secret skill", content="TOP SECRET PLAYBOOK",
    )
    await mods["skill"].set_routine_skill_autoload(
        victim_routine["id"], secret_skill["id"], True,
    )

    attacker_project = await mods["project"].create_project(attacker["id"], name="Attacker P")
    return victim, victim_project, victim_routine, secret_skill, attacker, attacker_project


def _create(project_id, routine_id, user):
    from chat.project_routes import (
        CreateProjectConversationRequest,
        create_project_conversation,
    )
    return _run(create_project_conversation(
        project_id, CreateProjectConversationRequest(routine_id=routine_id), user,
    ))


def test_foreign_routine_id_is_rejected(_isolated_storage):
    mods = _isolated_storage
    victim, victim_project, victim_routine, _skill, attacker, attacker_project = _run(_seed(mods))

    with pytest.raises(HTTPException) as exc:
        _create(attacker_project["id"], victim_routine["id"], attacker)
    assert exc.value.status_code == 404
    assert exc.value.detail["message"] == "Routine not found"

    # Nothing was persisted with the foreign id.
    rows = _run(mods["conv"].list_project_conversations_meta(attacker_project["id"]))
    assert rows == []


def test_unknown_routine_id_is_a_404_not_a_500(_isolated_storage):
    mods = _isolated_storage
    victim, victim_project, *_ = _run(_seed(mods))

    # Before the fix this hit the enforced routines FK and raised
    # IntegrityError out of the route.
    with pytest.raises(HTTPException) as exc:
        _create(victim_project["id"], str(uuid.uuid4()), victim)
    assert exc.value.status_code == 404


def test_own_routine_in_another_project_is_rejected(_isolated_storage):
    mods = _isolated_storage
    victim, victim_project, victim_routine, *_ = _run(_seed(mods))
    other_project = _run(mods["project"].create_project(victim["id"], name="Other P"))

    with pytest.raises(HTTPException) as exc:
        _create(other_project["id"], victim_routine["id"], victim)
    assert exc.value.status_code == 404


def test_own_routine_in_its_project_is_accepted(_isolated_storage):
    mods = _isolated_storage
    victim, victim_project, victim_routine, *_ = _run(_seed(mods))

    response = _create(victim_project["id"], victim_routine["id"], victim)
    assert response["project_id"] == victim_project["id"]

    meta = _run(mods["conv"].get_conversation_meta(victim["id"], response["id"]))
    assert meta["routine_id"] == victim_routine["id"]


def test_resolver_is_scoped_to_owner_and_project(_isolated_storage):
    """Defense in depth: even if a foreign routine id ends up on a
    conversation row, resolving its auto-loads for another owner (or
    another project) yields nothing."""
    mods = _isolated_storage
    victim, victim_project, victim_routine, secret_skill, attacker, attacker_project = _run(_seed(mods))
    resolve = mods["skill"].get_routine_autoloaded_skills

    own = _run(resolve(victim_routine["id"], victim["id"], victim_project["id"]))
    assert [s["id"] for s in own] == [secret_skill["id"]]

    assert _run(resolve(victim_routine["id"], attacker["id"], attacker_project["id"])) == []
    assert _run(resolve(victim_routine["id"], attacker["id"], victim_project["id"])) == []
    assert _run(resolve(victim_routine["id"], victim["id"], attacker_project["id"])) == []
