"""Central conversation/project path resolvers and ownership accessors.

Follow-up to security finding #279217 (issue #185): turning an id into an
on-disk path happens ONLY in ``_cs().get_conversation_dir`` /
``get_project_dir``, which reject non-canonical ids and refuse paths that
escape ``CHATS_DIR`` / ``PROJECTS_DIR``; HTTP routes add the DB ownership
check through ``chat.conversation_access``.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import chat.storage as storage_mod


# Other suites (test_storage_seq.py) reload chat.storage, so bind the class and
# the exception through the module at call time, never at import time.
def _cs():
    return storage_mod.ChatStorage


def _invalid():
    return storage_mod.InvalidStorageIdError


def _run(coro):
    return asyncio.run(coro)


BAD_IDS = ["", ".", "..", "a/b", "../x", "x/..", "/abs", "/", "owned/workspace/cache", "a\\b/c"]


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    chats = tmp_path / "chats"
    projects = tmp_path / "projects"
    monkeypatch.setattr(storage_mod, "CHATS_DIR", chats, raising=True)
    monkeypatch.setattr(storage_mod, "PROJECTS_DIR", projects, raising=True)
    return chats, projects


# ---------------------------------------------------------------------------
# Canonical-id validation
# ---------------------------------------------------------------------------


class TestCanonicalIds:
    @pytest.mark.parametrize("bad", BAD_IDS)
    def test_conversation_dir_rejects_non_canonical(self, roots, bad):
        with pytest.raises(_invalid()):
            _cs().get_conversation_dir(bad)

    @pytest.mark.parametrize("bad", BAD_IDS)
    def test_project_dir_rejects_non_canonical(self, roots, bad):
        with pytest.raises(_invalid()):
            _cs().get_project_dir(bad)
        with pytest.raises(_invalid()):
            _cs().get_project_db_path(bad)

    @pytest.mark.parametrize("bad", BAD_IDS)
    def test_workspace_path_rejects_non_canonical(self, roots, bad, monkeypatch):
        async def no_project(_cid):
            return None

        monkeypatch.setattr("db.conversation_store.get_project_for_conversation", no_project)
        with pytest.raises(_invalid()):
            _run(_cs().get_workspace_path(bad))
        if bad:  # an empty project_id has always meant "standalone conversation"
            with pytest.raises(_invalid()):
                _run(_cs().get_workspace_path("fine", project_id=bad))

    def test_non_string_ids_rejected(self, roots):
        for value in (None, 12, b"conv"):
            with pytest.raises(_invalid()):
                _cs().get_conversation_dir(value)  # type: ignore[arg-type]

    def test_error_is_a_value_error(self):
        assert issubclass(_invalid(), ValueError)

    def test_private_alias_is_the_same_resolver(self, roots):
        chats, _ = roots
        assert _cs()._get_conversation_dir("c1") == chats / "c1"
        with pytest.raises(_invalid()):
            _cs()._get_conversation_dir("../c1")


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------


class TestContainment:
    def test_plain_join_is_returned_unresolved(self, roots):
        chats, projects = roots
        assert _cs().get_conversation_dir("conv") == chats / "conv"
        assert _cs().get_project_dir("proj") == projects / "proj"
        assert _cs().get_project_db_path("proj") == projects / "proj" / "project.db"
        assert _run(_cs().get_workspace_path("conv", project_id="proj")) == (
            projects / "proj" / "workspace"
        )

    def test_missing_root_is_fine(self, roots):
        # Roots are created lazily by create_conversation; resolving before
        # that must not require the directory to exist.
        chats, _ = roots
        assert not chats.exists()
        assert _cs().get_conversation_dir("conv") == chats / "conv"

    def test_symlink_escaping_chats_dir_is_rejected(self, roots, tmp_path):
        chats, projects = roots
        chats.mkdir()
        projects.mkdir()
        outside = tmp_path / "host-secret"
        outside.mkdir()
        (chats / "evil").symlink_to(outside, target_is_directory=True)
        (projects / "evil").symlink_to(outside, target_is_directory=True)

        with pytest.raises(_invalid()):
            _cs().get_conversation_dir("evil")
        with pytest.raises(_invalid()):
            _cs().get_project_dir("evil")
        with pytest.raises(_invalid()):
            _run(_cs().get_workspace_path("x", project_id="evil"))

    def test_dangling_symlink_pointing_outside_is_rejected(self, roots, tmp_path):
        chats, _ = roots
        chats.mkdir()
        (chats / "dangling").symlink_to(tmp_path / "does-not-exist")
        with pytest.raises(_invalid()):
            _cs().get_conversation_dir("dangling")

    def test_symlink_staying_inside_tree_is_allowed(self, roots):
        chats, _ = roots
        (chats / "real").mkdir(parents=True)
        (chats / "alias").symlink_to(chats / "real", target_is_directory=True)
        assert _cs().get_conversation_dir("alias") == chats / "alias"

    def test_symlinked_root_is_allowed(self, tmp_path, monkeypatch):
        # A data dir that is itself a symlink (common deployment layout) must
        # not trip the containment check.
        real = tmp_path / "real-chats"
        (real / "conv").mkdir(parents=True)
        link = tmp_path / "chats-link"
        link.symlink_to(real, target_is_directory=True)
        monkeypatch.setattr(storage_mod, "CHATS_DIR", link, raising=True)
        assert _cs().get_conversation_dir("conv") == link / "conv"


# ---------------------------------------------------------------------------
# Ownership accessors
# ---------------------------------------------------------------------------


def _patch_store(monkeypatch, owned: dict[str, dict], projects: dict[str, dict]):
    async def get_meta(user_id, conversation_id):
        row = owned.get(conversation_id)
        return row if row and row["user_id"] == user_id else None

    async def get_project(user_id, project_id):
        row = projects.get(project_id)
        return row if row and row["user_id"] == user_id else None

    async def project_for(conversation_id):
        row = owned.get(conversation_id)
        return row.get("project_id") if row else None

    monkeypatch.setattr("db.conversation_store.get_conversation_meta", get_meta, raising=True)
    monkeypatch.setattr(
        "db.conversation_store.get_project_for_conversation", project_for, raising=True
    )
    monkeypatch.setattr("db.project_store.get_project", get_project, raising=True)


class TestOwnershipAccessors:
    def test_resolve_owned_workspace(self, roots, monkeypatch):
        from chat.conversation_access import resolve_owned_workspace

        chats, projects = roots
        _patch_store(
            monkeypatch,
            owned={
                "solo": {"id": "solo", "user_id": 1, "project_id": None},
                "inproj": {"id": "inproj", "user_id": 1, "project_id": "p1"},
                "theirs": {"id": "theirs", "user_id": 2, "project_id": None},
            },
            projects={},
        )

        meta, path = _run(resolve_owned_workspace(1, "solo"))
        assert meta["id"] == "solo" and path == chats / "solo"
        meta, path = _run(resolve_owned_workspace(1, "inproj"))
        assert path == projects / "p1" / "workspace"

        for cid in ("theirs", "missing", "../solo", "solo/workspace"):
            with pytest.raises(HTTPException) as exc:
                _run(resolve_owned_workspace(1, cid))
            assert exc.value.status_code == 404
            assert exc.value.detail["error"] == "conversation_not_found"

    def test_resolve_owned_project_dir(self, roots, monkeypatch):
        from chat.conversation_access import require_owned_project, resolve_owned_project_dir

        _, projects = roots
        _patch_store(
            monkeypatch,
            owned={},
            projects={
                "mine": {"id": "mine", "user_id": 1},
                "theirs": {"id": "theirs", "user_id": 2},
            },
        )
        project, path = _run(resolve_owned_project_dir(1, "mine"))
        assert project["id"] == "mine" and path == projects / "mine"
        for pid in ("theirs", "missing", "../mine"):
            with pytest.raises(HTTPException) as exc:
                _run(require_owned_project(1, pid))
            assert exc.value.status_code == 404
            assert exc.value.detail["error"] == "not_found"


# ---------------------------------------------------------------------------
# Route regressions: user A cannot target user B's ids
# ---------------------------------------------------------------------------


def _build_client(monkeypatch, roots):
    from chat.auth import get_current_user_cookie_or_apikey_checked
    from chat.file_routes import router as file_router
    from chat.project_db_routes import router as project_db_router

    chats, projects = roots
    _patch_store(
        monkeypatch,
        owned={
            "mine": {"id": "mine", "user_id": 1, "project_id": None},
            "victim": {"id": "victim", "user_id": 2, "project_id": None},
        },
        projects={
            "myproj": {"id": "myproj", "user_id": 1},
            "victimproj": {"id": "victimproj", "user_id": 2},
        },
    )
    for cid in ("mine", "victim"):
        ws = chats / cid / "workspace"
        ws.mkdir(parents=True)
        (ws / "secret.txt").write_text(f"{cid} secret")

    app = FastAPI()
    app.include_router(file_router)
    app.include_router(project_db_router)

    async def user_a():
        return {"id": 1, "email": "a@example.test"}

    app.dependency_overrides[get_current_user_cookie_or_apikey_checked] = user_a
    return TestClient(app)


class TestCrossUserRoutes:
    def test_file_routes_reject_other_users_conversation(self, roots, monkeypatch):
        client = _build_client(monkeypatch, roots)

        ok = client.get("/app/api/conversations/mine/files")
        assert ok.status_code == 200, ok.text
        assert [f["name"] for f in ok.json()["files"]] == ["secret.txt"]

        for cid in ("victim", "victim/workspace", "../victim", "missing"):
            listed = client.get(f"/app/api/conversations/{cid}/files")
            assert listed.status_code == 404, (cid, listed.text)
            content = client.get(
                f"/app/api/conversations/{cid}/files/content", params={"path": "secret.txt"}
            )
            assert content.status_code == 404, (cid, content.text)
            assert "victim secret" not in content.text

    def test_project_table_routes_reject_other_users_project(self, roots, monkeypatch):
        client = _build_client(monkeypatch, roots)
        assert client.get("/app/api/projects/myproj/tables").status_code == 200
        for pid in ("victimproj", "missing", "../myproj"):
            r = client.get(f"/app/api/projects/{pid}/tables")
            assert r.status_code == 404, (pid, r.text)

    def test_gmail_draft_rejects_other_users_workspace_attachment(self, roots, monkeypatch):
        from api.gmail import draft_endpoints
        from api.gmail.models import CreateDraftRequest, DraftAttachment

        _patch_store(
            monkeypatch,
            owned={"victim": {"id": "victim", "user_id": 2, "project_id": None}},
            projects={},
        )

        async def creds(_user):
            return object()

        monkeypatch.setattr(draft_endpoints, "get_valid_service_credentials", creds)
        resolved: list = []

        async def spy(*args, **kwargs):
            resolved.append(args)
            return {"data": b"", "filename": "x", "mime_type": "text/plain"}

        monkeypatch.setattr(draft_endpoints, "_resolve_workspace_attachment", spy)

        request = CreateDraftRequest(
            to="x@example.test",
            subject="s",
            body="b",
            conversation_id="victim",
            attachments=[DraftAttachment(type="workspace", workspace_path="secret.txt")],
        )
        with pytest.raises(HTTPException) as exc:
            _run(draft_endpoints.create_draft(request, {"id": 1, "email": "a@example.test"}))
        assert exc.value.status_code == 404
        assert resolved == [], "attachment was resolved before the ownership check"
