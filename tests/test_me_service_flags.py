"""Tests for GET /me service-connection flags (chat/routes/user.py).

``has_any_service_connected`` is derived from the full
``get_user_connected_services()`` roster, so services the old hand-written
OR missed (airtable) and plugin services all count.
"""

import asyncio
from unittest.mock import patch

import chat.routes.user as user_routes
from config import plugins as plugins_mod
from config.plugin_types import QuestPlugin, UserConnectionSpec


def _run(coro):
    return asyncio.run(coro)


def _me(user, loaded_plugins=()):
    with patch.object(plugins_mod, "_LOADED", list(loaded_plugins)), \
            patch("config.service_credentials.read_service_credentials",
                  return_value=None):
        return _run(user_routes.get_current_user_info(user=user))


_BASE_USER = {"email": "u@example.com", "name": "U"}


def test_no_services_connected():
    info = _me(dict(_BASE_USER))
    assert info["google_services_connected"] is False
    assert info["has_any_service_connected"] is False


def test_airtable_counts_toward_aggregate():
    # This was missing from the old hand-written OR. (Twitter, the other
    # miss, is a plugin service now -- covered by the plugin test below.)
    info = _me({**_BASE_USER, "airtable_token": "pat123"})
    assert info["has_any_service_connected"] is True


def test_google_services_flag_still_reported():
    info = _me({**_BASE_USER, "google_services_oauth": {"scopes": []}})
    assert info["google_services_connected"] is True
    assert info["has_any_service_connected"] is True


def test_connected_plugin_counts_toward_aggregate():
    plugin = QuestPlugin(
        id="acme", label="Acme",
        user_connection=UserConnectionSpec(
            kind="api_key",
            connected=lambda row: bool(row.get("secret")),
        ),
    )
    user = {
        **_BASE_USER,
        "service_credentials": {"acme": {"service": "acme", "secret": "sk-1"}},
    }
    assert _me(user, [plugin])["has_any_service_connected"] is True
    # Not keyed -> the plugin contributes nothing.
    assert _me(dict(_BASE_USER), [plugin])["has_any_service_connected"] is False
