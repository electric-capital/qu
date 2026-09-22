"""Tests for the admin System Reports "Guides" section.

Covers the two store queries behind ``GET /admin/system-monitor/guides-report``
(``guide_store.list_all_guides`` and ``project_store.list_all_project_guides``)
and the endpoint's row assembly/sort order, against a throwaway sqlite file.
"""

import asyncio
import os
import shutil
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def _isolated_db(monkeypatch):
    """Swap the store modules' session factories onto a fresh sqlite file.

    Same no-reload pattern as tests/test_convert_conversation_to_project.py:
    monkeypatch the ``AsyncSessionLocal`` attribute on the already-loaded
    modules so ``patch("...")`` targets elsewhere in the session stay valid.
    """
    tmpdir = tempfile.mkdtemp(prefix="quest_guides_report_test_")
    db_path = os.path.join(tmpdir, "quest.db")

    sync_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False},
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    test_session_local = async_sessionmaker(async_engine, expire_on_commit=False)

    import db.models as models_mod
    import db.guide_store as guide_store_mod
    import db.project_store as project_store_mod
    import db.routine_store as routine_store_mod

    models_mod.Base.metadata.create_all(sync_engine)

    for mod in (guide_store_mod, project_store_mod, routine_store_mod):
        monkeypatch.setattr(mod, "AsyncSessionLocal", test_session_local)

    yield models_mod, guide_store_mod, project_store_mod, routine_store_mod

    _run(async_engine.dispose())
    sync_engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def _create_user(models_mod, session_factory, email, name=None):
    async with session_factory() as db:
        u = models_mod.User(
            email=email, api_key=f"k-{uuid.uuid4().hex}", name=name,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


@pytest.fixture()
def _seed(_isolated_db):
    """Two users: one with a default + named guide (the named one used by a
    routine) and a project with instructions; one with only an empty default
    guide and an instruction-less project."""
    models_mod, guide_store, project_store, routine_store = _isolated_db
    factory = guide_store.AsyncSessionLocal

    async def _create():
        alice = await _create_user(models_mod, factory, "alice@example.com", "Alice")
        bob = await _create_user(models_mod, factory, "bob@example.com", None)

        a_default = await guide_store.create_guide(
            alice, "Default", content="Be terse.", is_default=True,
        )
        a_named = await guide_store.create_guide(alice, "Analyst", content="x" * 120)
        b_default = await guide_store.create_guide(bob, "Default", content="", is_default=True)

        a_project = await project_store.create_project(alice, name="Research", guide="Cite sources.")
        b_project = await project_store.create_project(bob, name="Empty", guide="")

        await routine_store.create_routine(
            alice, a_project["id"], "Daily", "run", guide_id=a_named["id"],
        )
        await routine_store.create_routine(
            alice, a_project["id"], "Weekly", "run", guide_id=a_named["id"],
        )
        return {
            "alice": alice, "bob": bob,
            "a_default": a_default["id"], "a_named": a_named["id"],
            "b_default": b_default["id"],
            "a_project": a_project["id"], "b_project": b_project["id"],
        }

    return _run(_create())


def test_list_all_guides_reports_owner_length_and_routine_refs(_isolated_db, _seed):
    _, guide_store, _, _ = _isolated_db
    rows = _run(guide_store.list_all_guides())

    assert [(r["user_email"], r["name"]) for r in rows] == [
        ("alice@example.com", "Default"),
        ("alice@example.com", "Analyst"),
        ("bob@example.com", "Default"),
    ]
    by_id = {r["id"]: r for r in rows}
    named = by_id[_seed["a_named"]]
    assert named["routine_count"] == 2
    assert named["content_length"] == 120
    assert named["is_default"] is False
    assert named["user_name"] == "Alice"
    assert "content" not in named

    a_default = by_id[_seed["a_default"]]
    assert a_default["is_default"] is True
    assert a_default["routine_count"] == 0

    b_default = by_id[_seed["b_default"]]
    assert b_default["content_length"] == 0
    assert b_default["user_name"] == ""


def test_list_all_project_guides_skips_empty_instructions(_isolated_db, _seed):
    _, _, project_store, _ = _isolated_db
    rows = _run(project_store.list_all_project_guides())

    assert [r["id"] for r in rows] == [_seed["a_project"]]
    row = rows[0]
    assert row["name"] == "Research"
    assert row["user_email"] == "alice@example.com"
    assert row["content_length"] == len("Cite sources.")
    assert row["public"] is False
    assert "guide" not in row


def test_guides_report_endpoint_merges_and_sorts(_isolated_db, _seed, monkeypatch):
    import chat.routes.admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda email: True)
    resp = _run(admin_mod.admin_guides_report(user={"email": "admin@example.com"}))
    rows = resp["guides"]

    # Owner email first; within an owner, user guides (default first) precede
    # the project rows.
    assert [(r["user_email"], r["kind"], r["name"]) for r in rows] == [
        ("alice@example.com", "user", "Default"),
        ("alice@example.com", "user", "Analyst"),
        ("alice@example.com", "project", "Research"),
        ("bob@example.com", "user", "Default"),
    ]
    project_row = rows[2]
    assert project_row["project_id"] == _seed["a_project"]
    assert project_row["is_default"] is None
    assert project_row["routine_count"] is None
    user_row = rows[1]
    assert user_row["project_id"] is None
    assert user_row["routine_count"] == 2
    # Every row carries the same key set so the FE table needs no per-kind shape.
    assert len({tuple(sorted(r)) for r in rows}) == 1


def test_guides_report_endpoint_rejects_non_admin(_isolated_db, monkeypatch):
    from fastapi import HTTPException
    import chat.routes.admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda email: False)
    with pytest.raises(HTTPException) as exc:
        _run(admin_mod.admin_guides_report(user={"email": "nobody@example.com"}))
    assert exc.value.status_code == 403
