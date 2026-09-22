"""Tests for the Twilio plugin's verification + templates router (plugins/twilio/verify.py).

The flow has no OAuth provider: start texts a code (stored as a salted
hash in the credential row's ``oauth_blob.pending``), verify checks it
and promotes the number to ``phone_number``. Coverage: phone validation,
resend cooldown, expiry, attempt cap, the pending-never-connected rule,
disconnect, the templates GET/PUT, and the popup page.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import plugins.twilio.verify as verify_mod
from plugins.twilio.templates import TEMPLATES_SETTINGS_KEY
from plugins.twilio.upstream import TwilioError, twilio_connected


def _run(coro):
    return asyncio.run(coro)


USER = {"id": 7, "email": "u@example.com", "settings": {}}
_CONFIG = {"account_sid": "AC" + "a" * 32, "auth_token": "t", "from_number": "+15550001111"}


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body
        self.cookies = cookies if cookies is not None else {verify_mod.COOKIE_NAME: "signed"}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _patch_user(user=USER):
    async def _fake(_cookie):
        return user
    return patch.object(verify_mod, "get_user_from_cookie", _fake)


class _Store:
    """In-memory stand-in for the user_service_credentials row."""

    def __init__(self, blob=None):
        self.blob = blob

    def patches(self):
        async def get_credential(user_id, service):
            assert service == "twilio"
            return {"oauth_blob": self.blob} if self.blob is not None else None

        async def upsert_credential(user_id, service, *, secret=None, oauth_blob=None):
            assert service == "twilio"
            self.blob = oauth_blob
            return {"oauth_blob": oauth_blob}

        async def delete_credential(user_id, service):
            self.blob = None
            return True

        return (
            patch.object(verify_mod, "get_credential", get_credential),
            patch.object(verify_mod, "upsert_credential", upsert_credential),
            patch.object(verify_mod, "delete_credential", delete_credential),
        )

    def patched(self):
        stack = contextlib.ExitStack()
        for p in self.patches():
            stack.enter_context(p)
        return stack


def _body(response) -> dict:
    return json.loads(response.body)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

def test_start_texts_code_and_stores_salted_hash():
    store = _Store()
    send = AsyncMock(return_value={"sid": "SM1"})
    with _patch_user(), store.patched(), \
            patch.object(verify_mod, "load_twilio_config", return_value=_CONFIG), \
            patch.object(verify_mod, "send_sms", send), \
            patch.object(verify_mod, "generate_code", return_value="824913"):
        out = _run(verify_mod.twilio_start_verification(
            _FakeRequest({"phone_number": " +1 (555) 123-4567 "}),
        ))

    assert out["success"] is True
    assert out["phone_number_masked"] == "+•••••••4567"
    # The text carries the code; the row carries only its salted hash.
    to, text, config = send.await_args.args
    assert to == "+15551234567" and "824913" in text and config is _CONFIG
    pending = store.blob["pending"]
    assert pending["phone_number"] == "+15551234567"
    assert "824913" not in json.dumps(pending)
    assert pending["code_hash"] == verify_mod.hash_code("824913", pending["salt"])
    assert pending["attempts"] == 0
    # A pending verification never reads as connected.
    assert not twilio_connected({"oauth_blob": store.blob})


def test_start_keeps_existing_verified_number_until_confirmed():
    store = _Store({"phone_number": "+15550009999", "verified_at": "x"})
    with _patch_user(), store.patched(), \
            patch.object(verify_mod, "load_twilio_config", return_value=_CONFIG), \
            patch.object(verify_mod, "send_sms", AsyncMock(return_value={})):
        _run(verify_mod.twilio_start_verification(_FakeRequest({"phone_number": "+15551234567"})))
    assert store.blob["phone_number"] == "+15550009999"
    assert store.blob["pending"]["phone_number"] == "+15551234567"
    assert twilio_connected({"oauth_blob": store.blob})


def test_start_rejects_number_without_country_code():
    store = _Store()
    send = AsyncMock()
    with _patch_user(), store.patched(), \
            patch.object(verify_mod, "load_twilio_config", return_value=_CONFIG), \
            patch.object(verify_mod, "send_sms", send):
        resp = _run(verify_mod.twilio_start_verification(_FakeRequest({"phone_number": "5551234567"})))
    assert resp.status_code == 400
    assert _body(resp)["error"] == "invalid_phone_number"
    send.assert_not_awaited()
    assert store.blob is None


def test_start_unconfigured_is_503():
    store = _Store()
    with _patch_user(), store.patched(), \
            patch.object(verify_mod, "load_twilio_config",
                         side_effect=HTTPException(status_code=500, detail="not configured")):
        resp = _run(verify_mod.twilio_start_verification(_FakeRequest({"phone_number": "+15551234567"})))
    assert resp.status_code == 503
    assert _body(resp)["error"] == "twilio_not_configured"


def test_start_resend_cooldown():
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    store = _Store({"pending": {"phone_number": "+15551234567", "sent_at": recent}})
    send = AsyncMock()
    with _patch_user(), store.patched(), \
            patch.object(verify_mod, "load_twilio_config", return_value=_CONFIG), \
            patch.object(verify_mod, "send_sms", send):
        resp = _run(verify_mod.twilio_start_verification(_FakeRequest({"phone_number": "+15551234567"})))
    assert resp.status_code == 429
    assert _body(resp)["error"] == "resend_too_soon"
    send.assert_not_awaited()


def test_start_send_failure_is_502_and_stores_nothing():
    store = _Store()
    with _patch_user(), store.patched(), \
            patch.object(verify_mod, "load_twilio_config", return_value=_CONFIG), \
            patch.object(verify_mod, "send_sms", AsyncMock(side_effect=TwilioError("nope"))):
        resp = _run(verify_mod.twilio_start_verification(_FakeRequest({"phone_number": "+15551234567"})))
    assert resp.status_code == 502
    assert _body(resp)["error"] == "sms_send_failed"
    assert store.blob is None


def test_start_requires_session():
    async def _none(_cookie):
        return None
    with patch.object(verify_mod, "get_user_from_cookie", _none), pytest.raises(HTTPException) as exc:
        _run(verify_mod.twilio_start_verification(_FakeRequest({"phone_number": "+15551234567"})))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def _pending(code="123456", *, expires_in=600, attempts=0, phone="+15551234567"):
    salt = "s4lt"
    return {
        "phone_number": phone,
        "code_hash": verify_mod.hash_code(code, salt),
        "salt": salt,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
        "attempts": attempts,
    }


def test_verify_success_promotes_number_and_invalidates_sessions():
    store = _Store({"phone_number": "+15550009999", "pending": _pending()})
    invalidate = patch("chat.gemini_api.invalidate_user_sessions")
    with _patch_user(), store.patched(), invalidate as inv:
        out = _run(verify_mod.twilio_verify_code(_FakeRequest({"code": " 123 456 "})))
    assert out == {"success": True, "phone_number": "+15551234567"}
    assert store.blob["phone_number"] == "+15551234567"
    assert "pending" not in store.blob
    assert "verified_at" in store.blob
    assert twilio_connected({"oauth_blob": store.blob})
    inv.assert_called_once_with(7)


def test_verify_wrong_code_counts_attempt():
    store = _Store({"pending": _pending()})
    with _patch_user(), store.patched():
        resp = _run(verify_mod.twilio_verify_code(_FakeRequest({"code": "000000"})))
    assert resp.status_code == 400
    assert _body(resp)["error"] == "incorrect_code"
    assert store.blob["pending"]["attempts"] == 1
    assert "phone_number" not in store.blob


def test_verify_attempt_cap_voids_code():
    store = _Store({"pending": _pending(attempts=verify_mod.MAX_ATTEMPTS - 1)})
    with _patch_user(), store.patched():
        resp = _run(verify_mod.twilio_verify_code(_FakeRequest({"code": "000000"})))
    assert _body(resp)["error"] == "too_many_attempts"
    assert "pending" not in store.blob
    # Even the right code no longer works.
    with _patch_user(), store.patched():
        resp = _run(verify_mod.twilio_verify_code(_FakeRequest({"code": "123456"})))
    assert _body(resp)["error"] == "no_pending_verification"


def test_verify_expired_code():
    store = _Store({"pending": _pending(expires_in=-1)})
    with _patch_user(), store.patched():
        resp = _run(verify_mod.twilio_verify_code(_FakeRequest({"code": "123456"})))
    assert _body(resp)["error"] == "code_expired"
    assert "pending" not in store.blob


@pytest.mark.parametrize("code", ["", "12345", "abcdef", None])
def test_verify_malformed_code_rejected_without_touching_store(code):
    store = _Store({"pending": _pending()})
    with _patch_user(), store.patched():
        resp = _run(verify_mod.twilio_verify_code(_FakeRequest({"code": code})))
    assert _body(resp)["error"] == "invalid_code"
    assert store.blob["pending"]["attempts"] == 0


def test_verify_without_pending():
    store = _Store(None)
    with _patch_user(), store.patched():
        resp = _run(verify_mod.twilio_verify_code(_FakeRequest({"code": "123456"})))
    assert _body(resp)["error"] == "no_pending_verification"


def test_invalid_json_body_is_400():
    with _patch_user(), pytest.raises(HTTPException) as exc:
        _run(verify_mod.twilio_verify_code(_FakeRequest(ValueError("bad json"))))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------

def test_disconnect_deletes_row():
    store = _Store({"phone_number": "+15551234567"})
    with _patch_user(), store.patched(), patch("chat.gemini_api.invalidate_user_sessions") as inv:
        out = _run(verify_mod.twilio_disconnect(_FakeRequest()))
    assert out == {"success": True}
    assert store.blob is None
    inv.assert_called_once_with(7)


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------

def test_get_templates_reports_trust_and_number():
    user = {
        **USER,
        "settings": {TEMPLATES_SETTINGS_KEY: [{"name": "a", "body": "x"}]},
        "service_credentials": {"twilio": {"oauth_blob": {"phone_number": "+15551234567"}}},
    }
    with _patch_user(user), patch.object(
        verify_mod, "load_twilio_config", return_value={**_CONFIG, "trusted_channel": True},
    ):
        out = _run(verify_mod.twilio_get_templates(_FakeRequest()))
    assert out["templates"] == [{"name": "a", "body": "x"}]
    assert out["trusted_channel"] is True
    assert out["phone_number"] == "+15551234567"


def test_get_templates_unconfigured_reads_untrusted():
    with _patch_user(), patch.object(
        verify_mod, "load_twilio_config", side_effect=HTTPException(status_code=500, detail="x"),
    ):
        out = _run(verify_mod.twilio_get_templates(_FakeRequest()))
    assert out["trusted_channel"] is False
    assert out["phone_number"] is None
    assert out["templates"] == []


def test_put_templates_validates_and_persists():
    saved = {}

    async def update(email, patch_dict):
        saved.update(patch_dict)
        return {**USER, "settings": dict(patch_dict)}

    with _patch_user(), patch.object(verify_mod, "update_user_settings", update), \
            patch.object(verify_mod, "load_twilio_config", return_value=_CONFIG):
        out = _run(verify_mod.twilio_put_templates(_FakeRequest({
            "templates": [{"name": " Deploy done ", "body": " ok "}],
        })))
    assert saved == {TEMPLATES_SETTINGS_KEY: [{"name": "Deploy done", "body": "ok"}]}
    assert out["templates"] == [{"name": "Deploy done", "body": "ok"}]


def test_put_templates_rejects_invalid():
    update = AsyncMock()
    with _patch_user(), patch.object(verify_mod, "update_user_settings", update):
        resp = _run(verify_mod.twilio_put_templates(_FakeRequest({
            "templates": [{"name": "a", "body": "x"}, {"name": "A", "body": "y"}],
        })))
    assert resp.status_code == 400
    assert _body(resp)["error"] == "invalid_templates"
    update.assert_not_awaited()


# ---------------------------------------------------------------------------
# popup page + router shape
# ---------------------------------------------------------------------------

def test_connect_page_shows_current_number_and_endpoints():
    user = {**USER, "service_credentials": {"twilio": {"oauth_blob": {"phone_number": "+15551234567"}}}}
    with _patch_user(user), patch.object(verify_mod, "load_twilio_config", return_value=_CONFIG):
        resp = _run(verify_mod.twilio_connect_page(_FakeRequest(), popup="1"))
    html = resp.body.decode()
    assert '"+15551234567"' in html
    assert "/auth/twilio/start" in html and "/auth/twilio/verify" in html
    assert "oauth_callback_success" in html


def test_connect_page_without_session():
    async def _none(_cookie):
        return None
    with patch.object(verify_mod, "get_user_from_cookie", _none):
        resp = _run(verify_mod.twilio_connect_page(_FakeRequest(cookies={}), popup="1"))
    assert b"Authentication Required" in resp.body


def test_connect_page_unconfigured():
    with _patch_user(), patch.object(
        verify_mod, "load_twilio_config", side_effect=HTTPException(status_code=500, detail="Twilio credentials not configured"),
    ):
        resp = _run(verify_mod.twilio_connect_page(_FakeRequest(), popup="1"))
    assert b"Twilio not configured" in resp.body


def test_every_route_lives_under_auth_twilio():
    paths = {route.path for route in verify_mod.router.routes}
    assert paths == {
        "/auth/twilio", "/auth/twilio/start", "/auth/twilio/verify",
        "/auth/twilio/disconnect", "/auth/twilio/templates",
    }
    assert all(p == "/auth/twilio" or p.startswith("/auth/twilio/") for p in paths)
