"""Handler-level tests for the Slack action-request handlers
(plugins/slack/handlers.py): unknown-parameter rejection (mirroring the
core suite in tests/test_action_request_unknown_params.py) and the
"created using Quest" attribution block.
"""

import pytest

import config.server_config as server_config
from plugins.slack.handlers import (
    SendSlackDmHandler,
    SendSlackMessageHandler,
    _quest_attribution_text,
)


# ---------------------------------------------------------------------------
# Unknown-parameter rejection
# ---------------------------------------------------------------------------

def test_send_slack_dm_rejects_unknown_field():
    handler = SendSlackDmHandler()
    with pytest.raises(ValueError, match="Unknown parameter for send_slack_dm"):
        handler.validate_params({
            "user_id": "U1",
            "message": "hi",
            "attachments": [{"x": 1}],
        })


def test_send_slack_message_rejects_unknown_field():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="Unknown parameter for send_slack_message") as exc:
        handler.validate_params({
            "channel_id": "C1",
            "message": "hi",
            "channel_name": "general",  # server-injected; model must not supply
        })
    assert "channel_name" in str(exc.value)


# ---------------------------------------------------------------------------
# Quest attribution (Slack mrkdwn block suffix)
# ---------------------------------------------------------------------------

def _set_config(monkeypatch, **config):
    monkeypatch.setattr(server_config, "load_server_config", lambda: config)


def test_slack_attribution_links_when_configured(monkeypatch):
    _set_config(monkeypatch, app_base_url="https://quest.example.com")
    assert (
        _quest_attribution_text("Alice")
        == "_Alice created and sent this message using "
        "<https://quest.example.com/|Quest>_"
    )


def test_slack_attribution_plain_text_when_unset(monkeypatch):
    _set_config(monkeypatch)
    assert _quest_attribution_text() == "_Created and sent using Quest_"


# ---------------------------------------------------------------------------
# Preview enrichment reads the credential-row token
# ---------------------------------------------------------------------------

def test_enrich_noops_without_connected_slack():
    import asyncio

    handler = SendSlackMessageHandler()
    params = {"channel_id": "C0123456789", "message": "hi"}
    # No service_credentials row -> no token -> enrichment must no-op
    # without raising or injecting fields.
    asyncio.run(handler.enrich_params_for_preview(params, {"email": "u@x"}))
    assert "channel_name" not in params
    assert "thread_context" not in params
