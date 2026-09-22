"""Tests for the create_drive_folder action_request handler.

Covers ``CreateDriveFolderHandler.validate_params`` (unknown-key
rejection, missing/empty name, parent_folder_id passthrough),
``render_preview`` (root default, injected parent_folder_name, raw-id
fallback) and ``execute`` (connection/scope pre-checks, folder creation
via a mocked ``make_authenticated_request`` on the shared ``_drive``
module, error mapping).
"""

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


def _handler():
    from chat.action_request_types.create_drive_folder import CreateDriveFolderHandler
    return CreateDriveFolderHandler()


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_handler_metadata():
    from db.models import ActionRequestType

    handler = _handler()
    assert handler.type_name == ActionRequestType.CREATE_DRIVE_FOLDER
    assert handler.display_name == "Create Drive Folder"
    assert handler.approve_label == "Create"


# ---------------------------------------------------------------------------
# validate_params
# ---------------------------------------------------------------------------


def test_validate_params_rejects_unknown_field():
    handler = _handler()
    with pytest.raises(ValueError, match="Unknown parameter for create_drive_folder"):
        handler.validate_params({"name": "Reports", "folder_id": "ABC"})


def test_validate_params_rejects_missing_name():
    handler = _handler()
    with pytest.raises(ValueError, match="name"):
        handler.validate_params({})


def test_validate_params_rejects_empty_name():
    handler = _handler()
    with pytest.raises(ValueError, match="non-empty"):
        handler.validate_params({"name": "   "})


def test_validate_params_rejects_non_string_name():
    handler = _handler()
    with pytest.raises(ValueError, match="name"):
        handler.validate_params({"name": 42})


def test_validate_params_trims_name():
    handler = _handler()
    out = handler.validate_params({"name": "  Q1 Reports  "})
    assert out == {"name": "Q1 Reports"}


def test_validate_params_keeps_drive_legal_name_verbatim():
    handler = _handler()
    # Folder names are Drive metadata, not filesystem paths -- no
    # filename sanitization.
    out = handler.validate_params({"name": "2026/06 Reports"})
    assert out["name"] == "2026/06 Reports"


def test_validate_params_parent_folder_id_passthrough():
    handler = _handler()
    out = handler.validate_params({"name": "Reports", "parent_folder_id": "  ABC123  "})
    assert out["parent_folder_id"] == "ABC123"


def test_validate_params_rejects_empty_parent_folder_id():
    handler = _handler()
    with pytest.raises(ValueError, match="parent_folder_id"):
        handler.validate_params({"name": "Reports", "parent_folder_id": "   "})


def test_validate_params_rejects_non_string_parent_folder_id():
    handler = _handler()
    with pytest.raises(ValueError, match="parent_folder_id"):
        handler.validate_params({"name": "Reports", "parent_folder_id": 42})


# ---------------------------------------------------------------------------
# render_preview
# ---------------------------------------------------------------------------


def test_render_preview_default_parent_root():
    handler = _handler()
    out = _run(handler.render_preview({"name": "Q1 Reports"}))
    assert {"key": "Folder", "value": "Q1 Reports"} in out
    assert {"key": "Parent", "value": "My Drive (root)"} in out


def test_render_preview_uses_injected_parent_folder_name():
    handler = _handler()
    out = _run(handler.render_preview({
        "name": "Q1 Reports",
        "parent_folder_id": "ABC",
        "parent_folder_name": "Quarterly",
    }))
    assert {"key": "Parent", "value": "Quarterly"} in out


def test_render_preview_falls_back_to_parent_id():
    handler = _handler()
    # No parent_folder_name injected and no user -> show the raw id.
    out = _run(handler.render_preview({"name": "Q1 Reports", "parent_folder_id": "ABC"}))
    assert {"key": "Parent", "value": "ABC"} in out


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def _user_with_drive_scope():
    return {
        "id": 1,
        "google_services_oauth": {
            "access_token": "tok",
            "scopes": ["https://www.googleapis.com/auth/drive.file"],
        },
    }


def test_execute_rejects_when_not_connected():
    handler = _handler()
    with pytest.raises(RuntimeError, match="not connected"):
        _run(handler.execute({"name": "Reports"}, {"id": 1, "google_services_oauth": {}}))


def test_execute_rejects_when_missing_scope():
    handler = _handler()
    user = {
        "id": 1,
        "google_services_oauth": {"access_token": "tok", "scopes": []},
    }
    with pytest.raises(RuntimeError, match="reconnect"):
        _run(handler.execute({"name": "Reports"}, user))


def test_execute_creates_folder_in_root(monkeypatch):
    handler = _handler()

    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "FOLDER123", "name": "Q1 Reports"}

    async def _fake_request(client, user, method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse()

    monkeypatch.setattr(
        "chat.action_request_types._drive.make_authenticated_request",
        _fake_request,
    )

    result = _run(handler.execute({"name": "Q1 Reports"}, _user_with_drive_scope()))

    assert result["success"] is True
    assert result["folder_id"] == "FOLDER123"
    assert result["name"] == "Q1 Reports"
    assert result["url"] == "https://drive.google.com/drive/folders/FOLDER123"
    assert captured["method"] == "POST"
    # Metadata base, not the upload base.
    assert captured["url"].startswith("https://www.googleapis.com/drive/v3/files")
    assert "supportsAllDrives=true" in captured["url"]
    assert captured["json"]["mimeType"] == "application/vnd.google-apps.folder"
    assert "parents" not in captured["json"]


def test_execute_creates_folder_with_parent(monkeypatch):
    handler = _handler()

    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "FOLDER456", "name": "Q1 Reports"}

    async def _fake_request(client, user, method, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _FakeResponse()

    monkeypatch.setattr(
        "chat.action_request_types._drive.make_authenticated_request",
        _fake_request,
    )

    result = _run(handler.execute(
        {"name": "Q1 Reports", "parent_folder_id": "PARENT1"},
        _user_with_drive_scope(),
    ))
    assert result["folder_id"] == "FOLDER456"
    assert captured["json"]["parents"] == ["PARENT1"]


def test_execute_parent_not_found_raises(monkeypatch):
    handler = _handler()

    class _FakeResponse:
        status_code = 404

        def json(self):
            return {"error": {"message": "File not found: PARENT1"}}

        text = ""

    async def _fake_request(client, user, method, url, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr(
        "chat.action_request_types._drive.make_authenticated_request",
        _fake_request,
    )

    with pytest.raises(RuntimeError, match="File not found: PARENT1"):
        _run(handler.execute(
            {"name": "Reports", "parent_folder_id": "PARENT1"},
            _user_with_drive_scope(),
        ))


def test_execute_insufficient_scope_maps_to_reauth(monkeypatch):
    handler = _handler()

    class _FakeResponse:
        status_code = 403

        def json(self):
            return {"error": {"message": "Insufficient Permission"}}

        text = ""

    async def _fake_request(client, user, method, url, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr(
        "chat.action_request_types._drive.make_authenticated_request",
        _fake_request,
    )

    with pytest.raises(RuntimeError, match="reconnect"):
        _run(handler.execute({"name": "Reports"}, _user_with_drive_scope()))
