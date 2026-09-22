"""Tests for TwilioSendSmsHandler (plugins/twilio/handlers.py).

The text-to-anyone action request: same-turn ``validate_params``
rejections (unknown keys, E.164 `to`, body limits), preview/summary
rendering, and execute's send + error conversion.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import plugins.twilio.handlers as handlers_mod
from plugins.twilio.handlers import TwilioSendSmsHandler
from plugins.twilio.upstream import TwilioError


def _run(coro):
    return asyncio.run(coro)


_CONFIG = {"account_sid": "AC" + "a" * 32, "auth_token": "t", "from_number": "+15550001111"}


class TestValidateParams:
    def test_normalizes_and_round_trips(self):
        out = TwilioSendSmsHandler().validate_params({
            "to": " +1 (555) 123-4567 ", "body": "  hi  ", "recipient_name": " Dana ",
        })
        assert out == {"to": "+15551234567", "body": "hi", "recipient_name": "Dana"}

    def test_recipient_name_optional(self):
        out = TwilioSendSmsHandler().validate_params({"to": "+15551234567", "body": "hi"})
        assert "recipient_name" not in out
        out = TwilioSendSmsHandler().validate_params({"to": "+15551234567", "body": "hi", "recipient_name": " "})
        assert "recipient_name" not in out

    @pytest.mark.parametrize("params,match", [
        ({"body": "hi"}, "to"),
        ({"to": "5551234567", "body": "hi"}, "country code"),
        ({"to": "+15551234567"}, "body"),
        ({"to": "+15551234567", "body": "  "}, "body"),
        ({"to": "+15551234567", "body": "x" * 1601}, "1600"),
        ({"to": "+15551234567", "body": "hi", "recipient_name": "n" * 121}, "recipient_name"),
        ({"to": "+15551234567", "body": "hi", "message": "x"}, "Unknown parameter"),
    ])
    def test_rejections(self, params, match):
        with pytest.raises(ValueError, match=match):
            TwilioSendSmsHandler().validate_params(params)


class TestRendering:
    def test_preview_and_summary(self):
        handler = TwilioSendSmsHandler()
        params = {"to": "+15551234567", "body": "hi", "recipient_name": "Dana"}
        assert _run(handler.render_preview(params)) == [
            {"key": "To", "value": "Dana (+15551234567)"},
            {"key": "Message", "value": "hi"},
        ]
        assert handler.summary_snippet(params) == "Dana: hi"
        assert handler.summary_snippet({"to": "+15551234567", "body": "hi"}) == "+15551234567: hi"
        assert handler.summary_snippet({}) == ""
        assert handler.approve_label == "Send"
        assert handler.resolved_label == "Sent"
        assert handler.type_name == "twilio_send_sms"


class TestExecute:
    def test_sends_via_admin_sender(self):
        send = AsyncMock(return_value={"sid": "SM9", "status": "queued"})
        with patch.object(handlers_mod, "load_twilio_config", return_value=_CONFIG), \
                patch.object(handlers_mod, "send_sms", send):
            out = _run(TwilioSendSmsHandler().execute(
                {"to": "+15551234567", "body": "hi"}, {"email": "u@example.com"},
            ))
        assert out == {"success": True, "to": "+15551234567", "message_sid": "SM9", "status": "queued"}
        assert send.await_args.args == ("+15551234567", "hi", _CONFIG)

    def test_unconfigured_raises_runtime_error(self):
        with patch.object(handlers_mod, "load_twilio_config",
                          side_effect=HTTPException(status_code=500, detail="not configured")), \
                pytest.raises(RuntimeError, match="not configured"):
            _run(TwilioSendSmsHandler().execute({"to": "+15551234567", "body": "hi"}, {}))

    def test_twilio_error_wrapped(self):
        with patch.object(handlers_mod, "load_twilio_config", return_value=_CONFIG), \
                patch.object(handlers_mod, "send_sms", AsyncMock(side_effect=TwilioError("blocked"))), \
                pytest.raises(RuntimeError, match="Failed to send SMS: blocked"):
            _run(TwilioSendSmsHandler().execute({"to": "+15551234567", "body": "hi"}, {}))
