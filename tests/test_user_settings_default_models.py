"""Tests for the per-visibility "last-used" composer model settings.

PUT /settings accepts ``default_model`` (private conversations) and
``public_default_model`` (public-project conversations) independently: each
is validated via ``resolve_model()``, cleared by an empty string, and left
untouched when omitted. GET /me surfaces both raw stored values. All DB calls
are monkeypatched -- no database is touched.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from chat.routes.user import UserSettingsUpdate, get_current_user_info, update_settings


_USER = {"id": 1, "email": "user@example.com"}


def _run(coro):
    return asyncio.run(coro)


def _put(**fields):
    update = AsyncMock(return_value={"settings": {}})
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", update), \
         patch("chat.gemini_api.invalidate_user_sessions"), \
         patch("chat.llm.config.resolve_model", lambda mid: object() if mid.startswith("known") else None):
        _run(update_settings(UserSettingsUpdate(**fields), user=_USER))
    return update.await_args.args[1]


@pytest.mark.parametrize("key", ["default_model", "public_default_model"])
def test_known_model_is_stored_under_its_own_key(key):
    stored = _put(**{key: "known-model"})
    assert stored == {key: "known-model"}


@pytest.mark.parametrize("key", ["default_model", "public_default_model"])
def test_empty_string_clears_the_stored_value(key):
    assert _put(**{key: ""}) == {key: None}


@pytest.mark.parametrize("key", ["default_model", "public_default_model"])
def test_unknown_model_rejected(key):
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", AsyncMock()) as update, \
         patch("chat.llm.config.resolve_model", lambda mid: None):
        with pytest.raises(HTTPException) as exc_info:
            _run(update_settings(UserSettingsUpdate(**{key: "nope"}), user=_USER))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_model"
    update.assert_not_awaited()


def test_the_two_keys_are_independent():
    # Writing the public pick never touches the private one and vice versa.
    assert "default_model" not in _put(public_default_model="known-public")
    assert "public_default_model" not in _put(default_model="known-private")


def test_me_surfaces_both_values():
    user = {
        **_USER,
        "settings": {"default_model": "known-private", "public_default_model": "known-public"},
    }
    with patch("api.instructions.get_user_connected_services", lambda u: {"google_services": False}), \
         patch("chat.routes.user.is_admin", lambda email: False), \
         patch("config.feature_gates.enabled_features", lambda email: []):
        info = _run(get_current_user_info(user=user))
    assert info["default_model"] == "known-private"
    assert info["public_default_model"] == "known-public"
