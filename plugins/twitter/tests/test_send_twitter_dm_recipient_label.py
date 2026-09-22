"""Approval-card recipient label must be bound to the delivery id.

Regression for a card-spoofing defect: a model-supplied ``recipient_name``
used to skip the Twitter lookup and become the card's sole "To" label,
while ``execute()`` delivered to the independent numeric id. These tests
drive the same server-side sequence as an action request -- validate,
enrich for preview, render the card, execute -- with only the Twitter
HTTP boundary faked.
"""

import asyncio

import pytest

from plugins.twitter import handlers as twitter_handlers
from plugins.twitter.handlers import SendTwitterDmHandler


class _TwitterResponse:
    def __init__(self, status_code=200, payload=None, text="OK"):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


_USER = {
    "id": 7,
    "email": "victim@example.test",
    "service_credentials": {
        "twitter": {"oauth_blob": {"access_token": "victim-twitter-token"}},
    },
}


def _install_fake_twitter(monkeypatch, *, lookup_ok=True):
    calls = []
    sent = []

    async def fake_twitter_request(user, method, path, *, params=None, json_body=None):
        calls.append((method, path))
        if method == "GET" and path == "users/424242":
            if not lookup_ok:
                return _TwitterResponse(status_code=500, text="boom")
            return _TwitterResponse(payload={
                "data": {"id": "424242", "username": "attacker_eve", "name": "Eve Attacker"},
            })
        if method == "POST" and path == "dm_conversations/with/424242/messages":
            sent.append(json_body["text"])
            return _TwitterResponse(payload={
                "data": {"dm_conversation_id": "conv-424242", "dm_event_id": "evt-1"},
            })
        raise AssertionError(f"unexpected Twitter API call: {method} {path}")

    monkeypatch.setattr(twitter_handlers, "twitter_request", fake_twitter_request)
    return calls, sent


def test_card_label_is_resolved_from_delivery_id(monkeypatch):
    calls, sent = _install_fake_twitter(monkeypatch)
    handler = SendTwitterDmHandler()

    async def drive():
        validated = handler.validate_params({
            "participant_id": "424242",
            "message": "Quest API key: qst_live_secret",
        })
        # Simulate a stale/pre-seeded label reaching the hook: it must be
        # discarded, never trusted.
        validated["recipient_name"] = "@trusted_cfo (Trusted CFO)"
        await handler.enrich_params_for_preview(validated, _USER)
        preview = await handler.render_preview(validated, _USER)
        result = await handler.execute(validated, _USER)
        return validated, preview, result

    validated, preview, result = asyncio.run(drive())

    by_key = {f["key"]: f["value"] for f in preview}
    assert by_key["To"] == "@attacker_eve (Eve Attacker)"
    assert validated["recipient_name"] == "@attacker_eve (Eve Attacker)"
    assert ("GET", "users/424242") in calls
    assert result["success"] is True
    assert sent == ["Quest API key: qst_live_secret"]


def test_card_falls_back_to_raw_id_when_lookup_fails(monkeypatch):
    _install_fake_twitter(monkeypatch, lookup_ok=False)
    handler = SendTwitterDmHandler()

    async def drive():
        validated = handler.validate_params({"participant_id": "424242", "message": "hi"})
        validated["recipient_name"] = "@trusted_cfo (Trusted CFO)"
        await handler.enrich_params_for_preview(validated, _USER)
        return validated, await handler.render_preview(validated, _USER)

    validated, preview = asyncio.run(drive())

    by_key = {f["key"]: f["value"] for f in preview}
    assert "recipient_name" not in validated
    assert by_key["To"] == "424242"
