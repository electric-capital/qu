"""Tests for the Twilio dynamic tools (plugins/twilio/tools.py) and plugin wiring.

The trusted-channel policy lives in ``twilio_send_self_sms``: free-form
``body`` only when the admin switch is on, ``template`` (sent verbatim)
always. Plus the registration fan-out via the ``twilio_plugin`` fixture.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import plugins.twilio.tools as tools_mod
from plugins.twilio.templates import TEMPLATES_SETTINGS_KEY
from plugins.twilio.tools import _tool_list_sms_templates, _tool_send_self_sms
from plugins.twilio.upstream import TwilioError


def _run(coro):
    return asyncio.run(coro)


_CONFIG = {"account_sid": "AC" + "a" * 32, "auth_token": "t", "from_number": "+15550001111"}
_TRUSTED = {**_CONFIG, "trusted_channel": True}
_TEMPLATES = [
    {"name": "Deploy done", "body": "Deploy finished."},
    {"name": "Call me", "body": "Please call when free."},
]


def _ctx(*, connected=True, templates=_TEMPLATES):
    user = {
        "id": 7,
        "email": "u@example.com",
        "settings": {TEMPLATES_SETTINGS_KEY: templates},
    }
    if connected:
        user["service_credentials"] = {"twilio": {"oauth_blob": {"phone_number": "+15551234567"}}}
    return SimpleNamespace(user=user, conversation_id="conv-1", project_id=None)


def _config(config):
    return patch.object(tools_mod, "load_twilio_config", return_value=config)


def _send(result=None, error=None):
    if error is not None:
        return patch.object(tools_mod, "send_sms", AsyncMock(side_effect=error))
    return patch.object(tools_mod, "send_sms", AsyncMock(return_value=result or {"sid": "SM1", "status": "queued"}))


# ---------------------------------------------------------------------------
# twilio_send_self_sms
# ---------------------------------------------------------------------------

class TestSendSelfSms:
    def test_trusted_free_form_sends_to_own_number(self):
        with _config(_TRUSTED), _send() as send:
            out = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "Build done"})))
        assert out["success"] is True
        assert out["free_form"] is True
        assert out["to"] == "+•••••••4567"  # never the full number
        assert out["message_sid"] == "SM1"
        to, body, config = send.await_args.args
        assert to == "+15551234567" and body == "Build done" and config is _TRUSTED

    def test_untrusted_free_form_refused_with_template_list(self):
        with _config(_CONFIG), _send() as send:
            out = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "Build done"})))
        assert out["error"] == "untrusted_channel"
        assert out["available_templates"] == ["Deploy done", "Call me"]
        send.assert_not_awaited()

    def test_untrusted_free_form_refused_mentions_empty_templates(self):
        with _config(_CONFIG), _send() as send:
            out = json.loads(_run(_tool_send_self_sms(_ctx(templates=[]), {"body": "x"})))
        assert out["error"] == "untrusted_channel"
        assert out["available_templates"] == []
        assert "Settings > SMS Messages" in out["message"]
        send.assert_not_awaited()

    def test_untrusted_template_sent_verbatim(self):
        with _config(_CONFIG), _send() as send:
            out = json.loads(_run(_tool_send_self_sms(_ctx(), {"template": " deploy done "})))
        assert out["success"] is True
        assert out["template"] == "Deploy done"
        assert out["body"] == "Deploy finished."
        assert send.await_args.args[1] == "Deploy finished."

    def test_trusted_template_also_works(self):
        with _config(_TRUSTED), _send():
            out = json.loads(_run(_tool_send_self_sms(_ctx(), {"template": "Call me"})))
        assert out["success"] is True and out["body"] == "Please call when free."

    def test_unknown_template(self):
        with _config(_CONFIG), _send() as send:
            out = json.loads(_run(_tool_send_self_sms(_ctx(), {"template": "nope"})))
        assert out["error"] == "unknown_template"
        assert out["available_templates"] == ["Deploy done", "Call me"]
        send.assert_not_awaited()

    def test_both_or_neither_rejected(self):
        with _config(_TRUSTED), _send() as send:
            both = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "x", "template": "Call me"})))
            neither = json.loads(_run(_tool_send_self_sms(_ctx(), {})))
            blank = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "   "})))
        assert both["error"] == "invalid_arguments"
        assert neither["error"] == "invalid_arguments"
        assert blank["error"] == "invalid_arguments"
        send.assert_not_awaited()

    def test_not_connected(self):
        with _config(_TRUSTED), _send() as send:
            out = json.loads(_run(_tool_send_self_sms(_ctx(connected=False), {"body": "x"})))
        assert out["error"] == "twilio_not_connected"
        send.assert_not_awaited()

    def test_not_configured(self):
        with patch.object(tools_mod, "load_twilio_config",
                          side_effect=HTTPException(status_code=500, detail="nope")), _send() as send:
            out = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "x"})))
        assert out["error"] == "twilio_not_configured"
        send.assert_not_awaited()

    def test_body_too_long(self):
        with _config(_TRUSTED), _send() as send:
            out = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "x" * 1601})))
        assert out["error"] == "body_too_long"
        send.assert_not_awaited()

    def test_twilio_failure_surfaced(self):
        with _config(_TRUSTED), _send(error=TwilioError("Twilio rejected the message: unreachable")):
            out = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "x"})))
        assert out["error"] == "sms_send_failed"
        assert "unreachable" in out["message"]

    def test_trusted_flag_read_fresh_per_call(self):
        # Flipping the admin switch between calls changes the verdict
        # without a restart (the config is re-read on every call).
        with _send():
            with _config(_CONFIG):
                first = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "x"})))
            with _config(_TRUSTED):
                second = json.loads(_run(_tool_send_self_sms(_ctx(), {"body": "x"})))
        assert first["error"] == "untrusted_channel"
        assert second["success"] is True


# ---------------------------------------------------------------------------
# twilio_list_sms_templates
# ---------------------------------------------------------------------------

class TestListTemplates:
    def test_untrusted_listing(self):
        with _config(_CONFIG):
            out = json.loads(_run(_tool_list_sms_templates(_ctx(), {})))
        assert out["connected"] is True
        assert out["trusted_channel"] is False
        assert out["free_form_allowed"] is False
        assert out["templates"] == _TEMPLATES
        assert out["phone_number_masked"] == "+•••••••4567"

    def test_trusted_listing(self):
        with _config(_TRUSTED):
            out = json.loads(_run(_tool_list_sms_templates(_ctx(connected=False), {})))
        assert out["connected"] is False
        assert out["free_form_allowed"] is True

    def test_not_configured(self):
        with patch.object(tools_mod, "load_twilio_config",
                          side_effect=HTTPException(status_code=500, detail="nope")):
            out = json.loads(_run(_tool_list_sms_templates(_ctx(), {})))
        assert out["error"] == "twilio_not_configured"


# ---------------------------------------------------------------------------
# Registration wiring
# ---------------------------------------------------------------------------

class TestPluginWiring:
    def test_manifest_shape(self, twilio_plugin):
        assert twilio_plugin.id == "twilio"
        assert {t.spec["name"] for t in twilio_plugin.tools} == {
            "twilio_list_sms_templates", "twilio_send_self_sms",
        }
        assert all(t.requires_service == "twilio" for t in twilio_plugin.tools)
        assert [h.type_name for h in twilio_plugin.action_request_handlers] == ["twilio_send_sms"]
        # No script-bridge access: sandbox code cannot text the user.
        assert twilio_plugin.script_tool_allowlist == frozenset()
        assert twilio_plugin.user_connection.kind == "oauth"
        keys = [f.key for f in twilio_plugin.credential_schema]
        assert keys == ["account_sid", "auth_token", "from_number", "trusted_channel"]
        assert next(f for f in twilio_plugin.credential_schema if f.key == "trusted_channel").type == "bool"

    def test_registered_into_core_registries(self, twilio_plugin):
        from chat.action_request_types.registry import get_handler
        from chat.gemini_api.tool_dispatch import TOOL_CALL_HANDLERS
        from chat.llm.tool_schemas import (
            ACTION_REQUEST_TYPE_ENUM,
            PUBLIC_TOOL_CALL_ALLOWLIST,
            TOOL_CALL_REGISTRY,
        )
        from chat.system_skills import CATALOG

        for name in ("twilio_list_sms_templates", "twilio_send_self_sms"):
            assert name in TOOL_CALL_REGISTRY
            assert TOOL_CALL_REGISTRY[name]["requires_service"] == "twilio"
            assert name in TOOL_CALL_HANDLERS
            assert name not in PUBLIC_TOOL_CALL_ALLOWLIST
        assert "twilio_send_sms" in ACTION_REQUEST_TYPE_ENUM
        assert get_handler("twilio_send_sms") is not None
        assert CATALOG["system:twilio"].requires == "twilio"

    def test_connectors_row_uses_popup_convention(self, twilio_plugin):
        from config.plugins import plugin_server_available

        spec = twilio_plugin.user_connection
        assert spec.connected({"oauth_blob": {"phone_number": "+15551234567"}})
        assert not spec.connected({"oauth_blob": {"pending": {}}})
        # Unconfigured store -> row hidden (available: false) like other plugins.
        with patch("config.service_credentials.read_service_credentials", return_value=None):
            assert plugin_server_available(twilio_plugin) is False
        with patch("config.service_credentials.read_service_credentials", return_value=_CONFIG):
            assert plugin_server_available(twilio_plugin) is True
