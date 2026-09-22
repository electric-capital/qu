"""Tests for the Settings > Slack pending-request reminder settings.

PUT /settings accepts ``slack_pending_notifications_enabled`` (bool; absent
means enabled) and ``slack_pending_notification_interval_minutes`` (int in
[MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES]; 0 clears the stored value so
the server default applies). All DB calls are monkeypatched -- no database
is touched.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from chat.routes.user import UserSettingsUpdate, update_settings
from chat.slack_notifier import MAX_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES


_USER = {"id": 1, "email": "user@example.com"}


def _run(coro):
    return asyncio.run(coro)


def _put(**fields):
    update = AsyncMock(return_value={"settings": dict(fields)})
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", update), \
         patch("chat.gemini_api.invalidate_user_sessions"):
        _run(update_settings(UserSettingsUpdate(**fields), user=_USER))
    return update.await_args.args[1]


@pytest.mark.parametrize("enabled", [True, False])
def test_enabled_flag_is_stored(enabled):
    assert _put(slack_pending_notifications_enabled=enabled) == {
        "slack_pending_notifications_enabled": enabled,
    }


@pytest.mark.parametrize("minutes", [MIN_INTERVAL_MINUTES, 60, 90, MAX_INTERVAL_MINUTES])
def test_valid_interval_is_stored(minutes):
    assert _put(slack_pending_notification_interval_minutes=minutes) == {
        "slack_pending_notification_interval_minutes": minutes,
    }


def test_zero_interval_clears_the_stored_value():
    assert _put(slack_pending_notification_interval_minutes=0) == {
        "slack_pending_notification_interval_minutes": None,
    }


@pytest.mark.parametrize(
    "minutes",
    [MIN_INTERVAL_MINUTES - 1, MAX_INTERVAL_MINUTES + 1, -60],
)
def test_out_of_range_interval_rejected(minutes):
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", AsyncMock()) as update:
        with pytest.raises(HTTPException) as exc_info:
            _run(update_settings(
                UserSettingsUpdate(
                    slack_pending_notification_interval_minutes=minutes,
                ),
                user=_USER,
            ))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_interval"
    update.assert_not_awaited()


def test_omitted_fields_leave_settings_untouched():
    update = AsyncMock(return_value={"settings": {}})
    with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
         patch("db.user_store.update_user_settings", update), \
         patch("chat.gemini_api.invalidate_user_sessions"):
        _run(update_settings(UserSettingsUpdate(default_model=""), user=_USER))
    stored = update.await_args.args[1]
    assert "slack_pending_notifications_enabled" not in stored
    assert "slack_pending_notification_interval_minutes" not in stored
