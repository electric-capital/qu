"""Tests for plugins/telegram/upstream.py: the admin credential loader
(store-then-legacy precedence, api_id normalization), the ``post_load``
legacy migration, the admin card round trip through the generic
endpoint, the per-user connection helpers, and TelegramClientManager's
connection/expiry errors (no network: the Telethon client is faked).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import config.service_credentials as sc
import plugins.telegram.upstream as upstream

LEGACY = {"api_id": 123456, "api_hash": "0123456789abcdef"}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path, monkeypatch, telegram_plugin):
    """Point the store and the plugin's legacy file at tmp_path."""
    monkeypatch.setattr(sc, "SERVICE_CREDENTIALS_DIR", tmp_path / "service_credentials")
    monkeypatch.setattr(sc, "LEGACY_CREDENTIALS_FILE", tmp_path / "server_credentials.json")
    legacy_file = tmp_path / "server_config.json"
    monkeypatch.setattr(upstream, "LEGACY_SERVER_CONFIG_FILE", legacy_file)
    return legacy_file


# ---------------------------------------------------------------------------
# Credential loader + legacy migration
# ---------------------------------------------------------------------------

def test_load_prefers_store_and_coerces_api_id(store):
    store.write_text(json.dumps({"telegram_app_info": {"api_id": 999, "api_hash": "legacy"}}))
    sc.write_service_credentials("telegram", {"api_id": "123456", "api_hash": "fromstore"})
    assert upstream.load_telegram_credentials() == {"api_id": 123456, "api_hash": "fromstore"}


def test_load_falls_back_to_legacy_server_config(store):
    store.write_text(json.dumps({"telegram_app_info": LEGACY}))
    assert upstream.load_telegram_credentials() == {"api_id": 123456, "api_hash": "0123456789abcdef"}


def test_load_unconfigured_raises(store):
    with pytest.raises(HTTPException) as exc_info:
        upstream.load_telegram_credentials()
    assert exc_info.value.status_code == 500
    # A "telegram" section in server_credentials.json is NOT the legacy
    # location -- only server_config.json's telegram_app_info is.
    sc.LEGACY_CREDENTIALS_FILE.write_text(json.dumps({"telegram": LEGACY}))
    with pytest.raises(HTTPException):
        upstream.load_telegram_credentials()


def test_load_rejects_non_numeric_api_id(store):
    sc.write_service_credentials("telegram", {"api_id": "abc", "api_hash": "h"})
    with pytest.raises(HTTPException) as exc_info:
        upstream.load_telegram_credentials()
    assert "integer" in str(exc_info.value.detail)


def test_post_load_migrates_legacy_section_normalized(store, telegram_plugin):
    store.write_text(json.dumps({"telegram_app_info": LEGACY, "other": {"x": 1}}))
    assert telegram_plugin.post_load is upstream.migrate_legacy_credentials
    assert upstream.migrate_legacy_credentials() is True
    # api_id lands as a string (the admin form's shape); the legacy file is untouched.
    assert sc.read_service_credentials("telegram") == {"api_id": "123456", "api_hash": "0123456789abcdef"}
    assert json.loads(store.read_text())["telegram_app_info"] == LEGACY
    # Idempotent.
    assert upstream.migrate_legacy_credentials() is False


def test_post_load_never_overwrites_store(store):
    store.write_text(json.dumps({"telegram_app_info": LEGACY}))
    sc.write_service_credentials("telegram", {"api_id": "1", "api_hash": "mine"})
    assert upstream.migrate_legacy_credentials() is False
    assert sc.read_service_credentials("telegram") == {"api_id": "1", "api_hash": "mine"}


def test_post_load_normalizes_verbatim_int_copy(store):
    # An older release copied the legacy section verbatim (int api_id).
    sc.write_service_credentials("telegram", LEGACY)
    assert upstream.migrate_legacy_credentials() is True
    assert sc.read_service_credentials("telegram") == {"api_id": "123456", "api_hash": "0123456789abcdef"}


def test_post_load_with_nothing_to_do(store):
    assert upstream.migrate_legacy_credentials() is False
    assert sc.read_service_credentials("telegram") is None


def test_validate_credentials_requires_numeric_api_id():
    assert upstream.validate_telegram_credentials({"api_id": " 42 ", "api_hash": "h"}) == {"api_id": "42", "api_hash": "h"}
    with pytest.raises(ValueError, match="API ID"):
        upstream.validate_telegram_credentials({"api_id": "not-a-number", "api_hash": "h"})


def test_is_configured():
    assert upstream.telegram_is_configured({"api_id": "1", "api_hash": "h"})
    assert not upstream.telegram_is_configured({"api_id": "1"})
    assert not upstream.telegram_is_configured({"api_id": "", "api_hash": "h"})


# ---------------------------------------------------------------------------
# Admin card through the generic endpoint
# ---------------------------------------------------------------------------

ADMIN_USER = {"id": 1, "email": "admin@example.com"}


@pytest.fixture
def admin_routes(store, monkeypatch):
    import chat.routes.admin as admin
    monkeypatch.setattr(admin, "is_admin", lambda email: email == ADMIN_USER["email"])
    return admin


def test_admin_put_writes_store_and_validates_api_id(admin_routes):
    detail = _run(admin_routes.admin_update_service_credentials(
        "telegram", {"api_id": "123456", "api_hash": "abcdef"}, user=ADMIN_USER,
    ))
    assert detail["configured"] is True
    assert detail["credentials"] == {"api_id": "123456", "api_hash_set": True}
    assert sc.read_service_credentials("telegram") == {"api_id": "123456", "api_hash": "abcdef"}

    with pytest.raises(HTTPException) as exc_info:
        _run(admin_routes.admin_update_service_credentials(
            "telegram", {"api_id": "not-a-number", "api_hash": "x"}, user=ADMIN_USER,
        ))
    assert exc_info.value.status_code == 400
    assert "API ID" in exc_info.value.detail["message"]


def test_admin_put_empty_hash_keeps_stored_one(admin_routes):
    sc.write_service_credentials("telegram", {"api_id": "123456", "api_hash": "keepme"})
    _run(admin_routes.admin_update_service_credentials(
        "telegram", {"api_id": "654321", "api_hash": ""}, user=ADMIN_USER,
    ))
    assert sc.read_service_credentials("telegram") == {"api_id": "654321", "api_hash": "keepme"}


def test_admin_get_after_migration_shows_string_api_id(admin_routes, store):
    store.write_text(json.dumps({"telegram_app_info": LEGACY}))
    upstream.migrate_legacy_credentials()
    detail = _run(admin_routes.admin_get_service_credentials("telegram", user=ADMIN_USER))
    assert detail["configured"] is True
    assert detail["source"] == "store"
    assert detail["credentials"] == {"api_id": "123456", "api_hash_set": True}
    assert "0123456789abcdef" not in json.dumps(detail)


def test_admin_list_includes_plugin_card(admin_routes):
    result = _run(admin_routes.admin_list_service_credentials(user=ADMIN_USER))
    services = {row["service"]: row for row in result["services"]}
    assert services["telegram"]["label"] == "Telegram"
    assert [f["key"] for f in services["telegram"]["fields"]] == ["api_id", "api_hash"]


# ---------------------------------------------------------------------------
# Per-user connection helpers
# ---------------------------------------------------------------------------

def test_session_helpers_and_connected_predicate():
    user = {"service_credentials": {"telegram": {"oauth_blob": {"session": "s", "phone": "+1"}}}}
    assert upstream.get_telegram_session(user) == "s"
    assert upstream.get_user_telegram_blob(user)["phone"] == "+1"
    assert upstream.get_telegram_session({}) is None
    assert upstream.get_telegram_session({"service_credentials": {"telegram": {"oauth_blob": {"pending": {}}}}}) is None
    assert upstream.telegram_connected({"oauth_blob": {"session": "s"}})
    assert not upstream.telegram_connected({"oauth_blob": {"pending": {"phone": "+1"}}})
    assert not upstream.telegram_connected({"oauth_blob": None})
    assert not upstream.telegram_connected({})


def test_connected_services_gate(telegram_plugin, monkeypatch):
    from api.instructions import get_user_connected_services
    from config import plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_loaded_plugins", lambda: (telegram_plugin,))
    with patch("config.service_credentials.read_service_credentials",
               return_value={"api_id": "1", "api_hash": "h"}):
        connected = {"service_credentials": {"telegram": {"oauth_blob": {"session": "s"}}}}
        assert get_user_connected_services(connected)["telegram"] is True
        assert get_user_connected_services({})["telegram"] is False
    with patch("config.service_credentials.read_service_credentials", return_value=None):
        assert get_user_connected_services(connected)["telegram"] is False


# ---------------------------------------------------------------------------
# TelegramClientManager
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, *, authorized=True, connect_error=None):
        self.authorized = authorized
        self.connect_error = connect_error
        self.connected = False

    async def connect(self):
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized


@pytest.fixture
def manager():
    m = upstream.TelegramClientManager.get_instance()
    m._clients.clear()
    m._locks.clear()
    yield m
    m._clients.clear()
    m._locks.clear()


_CONNECTED = {"id": 5, "email": "u@x", "service_credentials": {"telegram": {"oauth_blob": {"session": "s"}}}}


def test_manager_rejects_disconnected_user(manager):
    with pytest.raises(HTTPException) as exc_info:
        _run(manager.get_client({"id": 5, "email": "u@x"}))
    assert exc_info.value.detail["error"] == "telegram_not_connected"


def test_manager_caches_connected_client_and_drops_it(manager):
    client = _FakeClient()
    with patch.object(upstream, "create_telegram_client", return_value=client):
        assert _run(manager.get_client(_CONNECTED)) is client
        assert _run(manager.get_client(_CONNECTED)) is client
    assert manager._clients == {5: client}
    _run(manager.drop_client(5))
    assert manager._clients == {} and client.connected is False
    _run(manager.drop_client(5))  # no-op


def test_manager_unauthorized_session_is_expired(manager):
    client = _FakeClient(authorized=False)
    with patch.object(upstream, "create_telegram_client", return_value=client):
        with pytest.raises(HTTPException) as exc_info:
            _run(manager.get_client(_CONNECTED))
    assert exc_info.value.detail["error"] == "telegram_session_expired"
    assert client.connected is False
    assert manager._clients == {}


def test_manager_connect_failure_is_api_error(manager):
    client = _FakeClient(connect_error=OSError("no route"))
    with patch.object(upstream, "create_telegram_client", return_value=client):
        with pytest.raises(HTTPException) as exc_info:
            _run(manager.get_client(_CONNECTED))
    assert exc_info.value.detail["error"] == "telegram_api_error"
    assert "no route" in exc_info.value.detail["message"]


def test_resolve_dialog_name_never_raises():
    entity_user = type("U", (), {"first_name": "Ada", "last_name": "Lovelace"})()
    entity_chat = type("C", (), {"title": "Team"})()
    client = AsyncMock()
    client.get_entity = AsyncMock(side_effect=[entity_user, entity_chat, RuntimeError("x")])
    with patch.object(upstream.TelegramClientManager, "get_client", AsyncMock(return_value=client)):
        assert _run(upstream.resolve_dialog_name(1, _CONNECTED)) == "Ada Lovelace"
        assert _run(upstream.resolve_dialog_name(2, _CONNECTED)) == "Team"
        assert _run(upstream.resolve_dialog_name(3, _CONNECTED)) is None
    assert _run(upstream.resolve_dialog_name(4, {"id": 1, "email": "u@x"})) is None
