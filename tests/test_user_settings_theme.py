"""Tests for the Settings > Appearance ``theme`` user setting.

PUT /settings accepts ``theme`` in {"light", "dark", "auto"} (plus "" as an
alias for auto); auto is stored as None so a never-set user and an
explicitly-auto user look the same, and GET /me surfaces the stored value.
All DB calls are monkeypatched -- no database is touched.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from chat.routes.user import THEME_CHOICES, UserSettingsUpdate, update_settings


_USER = {"id": 1, "email": "user@example.com"}


def _run(coro):
    return asyncio.run(coro)


def _put(theme):
    update = AsyncMock(return_value={"settings": {"theme": theme}})
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", update), \
         patch("chat.gemini_api.invalidate_user_sessions"):
        _run(update_settings(UserSettingsUpdate(theme=theme), user=_USER))
    return update.await_args.args[1]


def test_theme_choices():
    assert THEME_CHOICES == ("light", "dark", "auto")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_explicit_theme_is_stored(theme):
    assert _put(theme) == {"theme": theme}


@pytest.mark.parametrize("theme", ["auto", ""])
def test_auto_and_empty_clear_the_stored_value(theme):
    assert _put(theme) == {"theme": None}


@pytest.mark.parametrize("theme", ["Light", "system", "blue"])
def test_unknown_theme_rejected(theme):
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", AsyncMock()) as update:
        with pytest.raises(HTTPException) as exc_info:
            _run(update_settings(UserSettingsUpdate(theme=theme), user=_USER))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_theme"
    update.assert_not_awaited()


def test_omitted_theme_leaves_settings_untouched():
    update = AsyncMock(return_value={"settings": {}})
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", update), \
         patch("chat.gemini_api.invalidate_user_sessions"):
        _run(update_settings(UserSettingsUpdate(default_model=""), user=_USER))
    assert "theme" not in update.await_args.args[1]
