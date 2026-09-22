"""Tests for SendTelegramMessageHandler (plugins/telegram/handlers.py).

Pins the dialog_id format checks on top of the required-field /
unknown-key validation so a malformed Telegram id surfaces as a
synchronous ``Invalid parameters: ...`` tool result on the model's same
turn instead of persisting to an action_requests row and exploding inside
``execute()`` with an opaque Telethon ``PeerIdInvalid`` (or, worse,
routing to a real but unintended peer). Plus the collapsed-card fields,
the preview-enrichment hook, and execute()'s not-connected guard.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from plugins.telegram.handlers import SendTelegramMessageHandler


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def handler():
    return SendTelegramMessageHandler()


# ---------------------------------------------------------------------------
# Happy paths -- the validator must round-trip a well-formed payload
# without rewriting the id (Telegram peer ids round-trip exactly into
# `client.send_message(entity=...)` inside execute()).
# ---------------------------------------------------------------------------

def test_accepts_positive_user_id(handler):
    out = handler.validate_params({"dialog_id": 123456789, "message": "hi"})
    assert out == {"dialog_id": 123456789, "message": "hi"}


def test_accepts_negative_basic_group_id(handler):
    assert handler.validate_params({"dialog_id": -100200, "message": "hi"})["dialog_id"] == -100200


def test_accepts_supergroup_marker_id(handler):
    out = handler.validate_params({"dialog_id": -1001234567890, "message": "hi"})
    assert out["dialog_id"] == -1001234567890


def test_accepts_digit_string_dialog_id(handler):
    # String input must round-trip to the int form so execute() gets a
    # consistent type regardless of whether the model passed an int or a
    # JSON-stringified id.
    assert handler.validate_params({"dialog_id": "123456789", "message": "hi"})["dialog_id"] == 123456789


def test_accepts_negative_digit_string_dialog_id(handler):
    out = handler.validate_params({"dialog_id": "-1001234567890", "message": "hi"})
    assert out["dialog_id"] == -1001234567890


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------

def test_missing_dialog_id_rejected(handler):
    with pytest.raises(ValueError, match="dialog_id"):
        handler.validate_params({"dialog_id": "", "message": "hi"})
    with pytest.raises(ValueError, match="dialog_id"):
        handler.validate_params({"dialog_id": None, "message": "hi"})


def test_missing_message_rejected(handler):
    with pytest.raises(ValueError, match="message"):
        handler.validate_params({"dialog_id": 123456789})
    with pytest.raises(ValueError, match="message"):
        handler.validate_params({"dialog_id": 123456789, "message": ""})
    with pytest.raises(ValueError, match="message"):
        handler.validate_params({"dialog_id": 123456789, "message": "   "})


def test_message_over_4096_chars_rejected(handler):
    with pytest.raises(ValueError, match="4096"):
        handler.validate_params({"dialog_id": 123456789, "message": "x" * 4097})


def test_unknown_top_level_key_rejected(handler):
    """Server-injected `dialog_name` must not be allow-listed."""
    with pytest.raises(ValueError, match="Unknown parameter for send_telegram_message") as exc:
        handler.validate_params({"dialog_id": 123456789, "message": "hi", "dialog_name": "Friend"})
    assert "dialog_name" in str(exc.value)


@pytest.mark.parametrize("bad", [
    "@alice", "https://t.me/alice", "+15551234567", "C0123456789", "  42  ",
    1.5, True, [123], {"id": 123}, 0, -100, 10**17, -(10**17),
])
def test_rejects_malformed_dialog_ids(handler, bad):
    with pytest.raises(ValueError, match="dialog_id"):
        handler.validate_params({"dialog_id": bad, "message": "hi"})


def test_error_message_truncates_long_offending_value(handler):
    long_bogus = "z" * 500  # not int-shaped, > 100 chars
    with pytest.raises(ValueError) as exc:
        handler.validate_params({"dialog_id": long_bogus, "message": "hi"})
    # The raw 500-char string must not be echoed back in full.
    assert "z" * 500 not in str(exc.value)
    assert "..." in str(exc.value)


# ---------------------------------------------------------------------------
# Card fields, preview enrichment, execute guard
# ---------------------------------------------------------------------------

def test_card_fields(handler):
    assert handler.type_name == "send_telegram_message"
    assert handler.approve_label == "Send"
    assert handler.resolved_label == "Sent"
    assert handler.summary_snippet({"message": "hello there"}) == "hello there"
    preview = _run(handler.render_preview({"dialog_id": 42, "message": "hi"}))
    assert preview == [{"key": "To", "value": "42"}, {"key": "Message", "value": "hi"}]
    preview = _run(handler.render_preview({"dialog_id": 42, "dialog_name": "Alice", "message": "hi"}))
    assert preview[0] == {"key": "To", "value": "Alice"}


def test_registered_via_plugin(telegram_plugin):
    from chat.action_request_types import get_handler
    from chat.llm.tool_schemas import ACTION_REQUEST_TYPE_ENUM

    assert isinstance(get_handler("send_telegram_message"), SendTelegramMessageHandler)
    assert "send_telegram_message" in ACTION_REQUEST_TYPE_ENUM


def test_enrich_derives_dialog_name_from_id(handler):
    params = {"dialog_id": 42, "message": "hi", "dialog_name": "Spoofed"}
    with patch("plugins.telegram.handlers.resolve_dialog_name", AsyncMock(return_value="Alice")) as resolve:
        _run(handler.enrich_params_for_preview(params, {"id": 1}))
    resolve.assert_awaited_once_with(42, {"id": 1})
    assert params["dialog_name"] == "Alice"

    params = {"dialog_id": 42, "message": "hi", "dialog_name": "Spoofed"}
    with patch("plugins.telegram.handlers.resolve_dialog_name", AsyncMock(return_value=None)):
        _run(handler.enrich_params_for_preview(params, {"id": 1}))
    assert "dialog_name" not in params


def test_execute_requires_connection(handler):
    with pytest.raises(RuntimeError, match="not connected"):
        _run(handler.execute({"dialog_id": 42, "message": "hi"}, {"id": 1, "email": "u@x"}))


def test_execute_escapes_and_appends_attribution(handler):
    sent = AsyncMock(return_value=type("Msg", (), {"id": 777})())
    client = type("Client", (), {"send_message": sent})()
    user = {"id": 1, "email": "u@x", "service_credentials": {"telegram": {"oauth_blob": {"session": "s"}}}}
    with patch("plugins.telegram.handlers.TelegramClientManager.get_client", AsyncMock(return_value=client)):
        out = _run(handler.execute({"dialog_id": 42, "message": "<b>hi</b>"}, user))
    assert out == {"success": True, "dialog_id": 42, "message_id": 777}
    kwargs = sent.await_args.kwargs
    assert kwargs["entity"] == 42 and kwargs["parse_mode"] == "html"
    assert kwargs["message"].startswith("&lt;b&gt;hi&lt;/b&gt;\n\n<i>")
