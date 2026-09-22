"""Tests for the Settings > Appearance ``color_theme`` user setting.

PUT /settings accepts ``color_theme`` in COLOR_THEME_CHOICES (plus "" to
clear, stored as None = the default palette) and rejects anything else with
``invalid_color_theme``; GET /me surfaces the stored value. All DB calls are
monkeypatched -- no database is touched.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from chat.routes.user import COLOR_THEME_CHOICES, UserSettingsUpdate, update_settings


_USER = {"id": 1, "email": "user@example.com"}


def _run(coro):
    return asyncio.run(coro)


def _put(color_theme):
    update = AsyncMock(return_value={"settings": {"color_theme": color_theme}})
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", update), \
         patch("chat.gemini_api.invalidate_user_sessions"):
        _run(update_settings(UserSettingsUpdate(color_theme=color_theme), user=_USER))
    return update.await_args.args[1]


def test_color_theme_choices():
    assert COLOR_THEME_CHOICES == ("prototype", "electric-blue", "alloy", "recall")


@pytest.mark.parametrize("color_theme", COLOR_THEME_CHOICES)
def test_known_theme_is_stored(color_theme):
    assert _put(color_theme) == {"color_theme": color_theme}


def test_empty_clears_the_stored_value():
    assert _put("") == {"color_theme": None}


@pytest.mark.parametrize("color_theme", ["Prototype", "electric_blue", "blue", "auto", "Alloy", "alloy-river", "Recall"])
def test_unknown_theme_rejected(color_theme):
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", AsyncMock()) as update:
        with pytest.raises(HTTPException) as exc_info:
            _run(update_settings(UserSettingsUpdate(color_theme=color_theme), user=_USER))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_color_theme"
    update.assert_not_awaited()


def test_omitted_color_theme_leaves_settings_untouched():
    update = AsyncMock(return_value={"settings": {}})
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", update), \
         patch("chat.gemini_api.invalidate_user_sessions"):
        _run(update_settings(UserSettingsUpdate(theme="dark"), user=_USER))
    assert "color_theme" not in update.await_args.args[1]
