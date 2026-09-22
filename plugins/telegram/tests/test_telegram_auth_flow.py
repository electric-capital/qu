"""Tests for the Telegram plugin's login router (plugins/telegram/auth.py).

The flow has no OAuth provider: ``send-code`` asks Telegram for a login
code and parks the phone / code hash / pre-auth session under
``oauth_blob.pending`` (server-side, encrypted at rest -- never a cookie),
``verify`` signs in with the code (or moves to the password stage when
the account has 2FA), ``2fa`` finishes with the cloud password, and a
successful sign-in replaces the blob with the authorized ``session``.
Coverage: phone validation, pending never counts as connected, expiry,
attempt cap, stage checks, 2FA branch, disconnect, the popup page, and
the router's namespace. Telethon is replaced by a fake client.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

import plugins.telegram.auth as auth_mod
from plugins.telegram.upstream import TelegramClientManager, telegram_connected


def _run(coro):
    return asyncio.run(coro)


USER = {"id": 7, "email": "u@example.com", "settings": {}}
_CREDS = {"api_id": 123456, "api_hash": "abcdef"}


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body
        self.cookies = cookies if cookies is not None else {auth_mod.COOKIE_NAME: "signed"}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeClient:
    """Stand-in for a Telethon client: records calls, raises on demand."""

    def __init__(self, session_string, *, sign_in_error=None, saved="sess-saved"):
        self.initial_session = session_string
        self.session = SimpleNamespace(save=lambda: saved)
        self.sign_in_error = sign_in_error
        self.sign_in_calls: list[tuple] = []
        self.connected = False
        self.logged_out = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def send_code_request(self, phone):
        self.sent_to = phone
        return SimpleNamespace(phone_code_hash="hash-1")

    async def sign_in(self, *args, **kwargs):
        self.sign_in_calls.append((args, kwargs))
        if self.sign_in_error is not None:
            raise self.sign_in_error

    async def log_out(self):
        self.logged_out = True


def _patch_user(user=USER):
    async def _fake(_cookie):
        return user
    return patch.object(auth_mod, "get_user_from_cookie", _fake)


def _patch_client(client):
    return patch.object(auth_mod, "create_telegram_client", lambda session="": client)


def _patch_creds(configured=True):
    if configured:
        return patch.object(auth_mod, "load_telegram_credentials", return_value=_CREDS)
    return patch.object(
        auth_mod, "load_telegram_credentials",
        side_effect=HTTPException(status_code=500, detail="Telegram API credentials not configured"),
    )


class _Store:
    """In-memory stand-in for the user_service_credentials row."""

    def __init__(self, blob=None):
        self.blob = blob

    def patched(self):
        async def get_credential(user_id, service):
            assert service == "telegram"
            return {"oauth_blob": self.blob} if self.blob is not None else None

        async def upsert_credential(user_id, service, *, secret=None, oauth_blob=None):
            assert service == "telegram"
            self.blob = oauth_blob
            return {"oauth_blob": oauth_blob}

        async def delete_credential(user_id, service):
            self.blob = None
            return True

        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(auth_mod, "get_credential", get_credential))
        stack.enter_context(patch.object(auth_mod, "upsert_credential", upsert_credential))
        stack.enter_context(patch.object(auth_mod, "delete_credential", delete_credential))
        return stack


def _body(response) -> dict:
    return json.loads(response.body)


def _pending(*, stage="code", expires_in=600, attempts=0, phone="+15551234567"):
    now = datetime.now(timezone.utc)
    return {
        "phone": phone,
        "phone_code_hash": "hash-1",
        "session": "pre-auth-session",
        "stage": stage,
        "started_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "attempts": attempts,
    }


# ---------------------------------------------------------------------------
# send-code
# ---------------------------------------------------------------------------

def test_send_code_parks_pending_login_server_side():
    store = _Store()
    client = _FakeClient("", saved="pre-auth-session")
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        out = _run(auth_mod.telegram_send_code(_FakeRequest({"phone": " +1 (555) 123-4567 "})))

    assert out["success"] is True
    assert out["phone_masked"] == "+•••••••4567"
    assert client.sent_to == "+15551234567"
    assert client.connected is False  # disconnected after the request
    pending = store.blob["pending"]
    assert pending["phone"] == "+15551234567"
    assert pending["phone_code_hash"] == "hash-1"
    assert pending["session"] == "pre-auth-session"
    assert pending["stage"] == auth_mod.STAGE_CODE
    assert pending["attempts"] == 0
    # A pending login never reads as connected.
    assert not telegram_connected({"oauth_blob": store.blob})


def test_send_code_keeps_existing_session_until_login_succeeds():
    store = _Store({"session": "old-session", "phone": "+15550009999"})
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(_FakeClient("")):
        _run(auth_mod.telegram_send_code(_FakeRequest({"phone": "+15551234567"})))
    assert store.blob["session"] == "old-session"
    assert store.blob["pending"]["phone"] == "+15551234567"
    assert telegram_connected({"oauth_blob": store.blob})


@pytest.mark.parametrize("phone", ["5551234567", "", None, "+1 555", 12345])
def test_send_code_rejects_bad_numbers_without_contacting_telegram(phone):
    store = _Store()
    client = _FakeClient("")
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        resp = _run(auth_mod.telegram_send_code(_FakeRequest({"phone": phone})))
    assert resp.status_code == 400
    assert _body(resp)["error"] == "invalid_phone_number"
    assert not hasattr(client, "sent_to")
    assert store.blob is None


def test_send_code_unconfigured_is_503():
    store = _Store()
    with _patch_user(), store.patched(), _patch_creds(configured=False):
        resp = _run(auth_mod.telegram_send_code(_FakeRequest({"phone": "+15551234567"})))
    assert resp.status_code == 503
    assert _body(resp)["error"] == "telegram_not_configured"


def test_send_code_flood_wait_is_429_and_stores_nothing():
    store = _Store()
    client = _FakeClient("")

    async def _flood(phone):
        raise FloodWaitError(request=None, capture=42)
    client.send_code_request = _flood
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        resp = _run(auth_mod.telegram_send_code(_FakeRequest({"phone": "+15551234567"})))
    assert resp.status_code == 429
    assert _body(resp)["error"] == "flood_wait"
    assert "42" in _body(resp)["message"]
    assert store.blob is None
    assert client.connected is False


def test_send_code_requires_session():
    async def _none(_cookie):
        return None
    with patch.object(auth_mod, "get_user_from_cookie", _none), pytest.raises(HTTPException) as exc:
        _run(auth_mod.telegram_send_code(_FakeRequest({"phone": "+15551234567"})))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# verify (code stage)
# ---------------------------------------------------------------------------

def test_verify_success_stores_session_and_refreshes_runtime_state():
    store = _Store({"session": "old-session", "pending": _pending()})
    client = _FakeClient("pre-auth-session", saved="authorized-session")
    drop = AsyncMock()
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client), \
            patch("chat.gemini_api.invalidate_user_sessions") as inv, \
            patch.object(TelegramClientManager, "drop_client", drop):
        out = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": " 12 345 "})))

    assert out == {"success": True, "needs_2fa": False, "phone_masked": "+•••••••4567"}
    assert client.sign_in_calls == [(("+15551234567", "12345"), {"phone_code_hash": "hash-1"})]
    assert store.blob["session"] == "authorized-session"
    assert store.blob["phone"] == "+15551234567"
    assert "pending" not in store.blob
    assert "connected_at" in store.blob
    assert telegram_connected({"oauth_blob": store.blob})
    inv.assert_called_once_with(7)
    drop.assert_awaited_once_with(7)
    assert client.connected is False


def test_verify_2fa_account_moves_to_password_stage():
    store = _Store({"pending": _pending()})
    client = _FakeClient(
        "pre-auth-session", saved="pre-auth-session-2",
        sign_in_error=SessionPasswordNeededError(request=None),
    )
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        out = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "12345"})))
    assert out == {"success": True, "needs_2fa": True}
    pending = store.blob["pending"]
    assert pending["stage"] == auth_mod.STAGE_PASSWORD
    assert pending["session"] == "pre-auth-session-2"
    assert pending["attempts"] == 0
    assert "session" not in store.blob  # still not connected


def test_verify_wrong_code_counts_attempt():
    store = _Store({"pending": _pending()})
    client = _FakeClient("pre-auth-session", sign_in_error=PhoneCodeInvalidError(request=None))
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        resp = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "00000"})))
    assert resp.status_code == 400
    assert _body(resp)["error"] == "incorrect_code"
    assert store.blob["pending"]["attempts"] == 1
    assert "session" not in store.blob


def test_verify_attempt_cap_voids_login():
    store = _Store({"pending": _pending(attempts=auth_mod.MAX_ATTEMPTS - 1)})
    client = _FakeClient("pre-auth-session", sign_in_error=PhoneCodeInvalidError(request=None))
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        resp = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "00000"})))
    assert _body(resp)["error"] == "too_many_attempts"
    assert store.blob is None
    # Even the right code no longer works.
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(_FakeClient("")):
        resp = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "12345"})))
    assert _body(resp)["error"] == "no_pending_login"


def test_verify_attempt_cap_keeps_existing_session():
    store = _Store({"session": "old", "pending": _pending(attempts=auth_mod.MAX_ATTEMPTS - 1)})
    client = _FakeClient("pre-auth-session", sign_in_error=PhoneCodeInvalidError(request=None))
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "00000"})))
    assert store.blob == {"session": "old"}


def test_verify_expired_pending():
    store = _Store({"pending": _pending(expires_in=-1)})
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(_FakeClient("")):
        resp = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "12345"})))
    assert _body(resp)["error"] == "code_expired"
    assert store.blob is None


def test_verify_telegram_says_code_expired():
    store = _Store({"pending": _pending()})
    client = _FakeClient("pre-auth-session", sign_in_error=PhoneCodeExpiredError(request=None))
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        resp = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "12345"})))
    assert _body(resp)["error"] == "code_expired"
    assert store.blob is None


@pytest.mark.parametrize("code", ["", "abc", None])
def test_verify_malformed_code_rejected_without_touching_store(code):
    store = _Store({"pending": _pending()})
    with _patch_user(), store.patched():
        resp = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": code})))
    assert _body(resp)["error"] == "invalid_code"
    assert store.blob["pending"]["attempts"] == 0


def test_verify_in_password_stage_is_rejected():
    store = _Store({"pending": _pending(stage="password")})
    with _patch_user(), store.patched():
        resp = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "12345"})))
    assert _body(resp)["error"] == "password_required"


def test_verify_without_pending():
    store = _Store(None)
    with _patch_user(), store.patched():
        resp = _run(auth_mod.telegram_verify_code(_FakeRequest({"code": "12345"})))
    assert _body(resp)["error"] == "no_pending_login"


def test_invalid_json_body_is_400():
    with _patch_user(), pytest.raises(HTTPException) as exc:
        _run(auth_mod.telegram_verify_code(_FakeRequest(ValueError("bad json"))))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# 2fa (password stage)
# ---------------------------------------------------------------------------

def test_password_success_completes_login():
    store = _Store({"pending": _pending(stage="password")})
    client = _FakeClient("pre-auth-session", saved="authorized-session")
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client), \
            patch("chat.gemini_api.invalidate_user_sessions") as inv:
        out = _run(auth_mod.telegram_verify_password(_FakeRequest({"password": "hunter2"})))
    assert out["success"] is True and out["needs_2fa"] is False
    assert client.sign_in_calls == [((), {"password": "hunter2"})]
    assert store.blob["session"] == "authorized-session"
    assert "pending" not in store.blob
    inv.assert_called_once_with(7)


def test_password_wrong_counts_attempt():
    store = _Store({"pending": _pending(stage="password")})
    client = _FakeClient("pre-auth-session", sign_in_error=PasswordHashInvalidError(request=None))
    with _patch_user(), store.patched(), _patch_creds(), _patch_client(client):
        resp = _run(auth_mod.telegram_verify_password(_FakeRequest({"password": "nope"})))
    assert _body(resp)["error"] == "incorrect_password"
    assert store.blob["pending"]["attempts"] == 1


def test_password_in_code_stage_is_rejected():
    store = _Store({"pending": _pending(stage="code")})
    with _patch_user(), store.patched():
        resp = _run(auth_mod.telegram_verify_password(_FakeRequest({"password": "x"})))
    assert _body(resp)["error"] == "code_required"


def test_password_empty_rejected():
    store = _Store({"pending": _pending(stage="password")})
    with _patch_user(), store.patched():
        resp = _run(auth_mod.telegram_verify_password(_FakeRequest({"password": ""})))
    assert _body(resp)["error"] == "invalid_password"


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------

def test_disconnect_logs_out_and_deletes_row():
    user = {**USER, "service_credentials": {"telegram": {"oauth_blob": {"session": "s"}}}}
    store = _Store({"session": "s"})
    client = _FakeClient("s")
    drop = AsyncMock()
    with _patch_user(user), store.patched(), _patch_creds(), _patch_client(client), \
            patch("chat.gemini_api.invalidate_user_sessions") as inv, \
            patch.object(TelegramClientManager, "drop_client", drop):
        out = _run(auth_mod.telegram_disconnect(_FakeRequest()))
    assert out == {"success": True}
    assert client.logged_out is True and client.connected is False
    assert store.blob is None
    inv.assert_called_once_with(7)
    drop.assert_awaited_once_with(7)


def test_disconnect_survives_failed_logout():
    user = {**USER, "service_credentials": {"telegram": {"oauth_blob": {"session": "s"}}}}
    store = _Store({"session": "s"})
    client = _FakeClient("s")

    async def _boom():
        raise RuntimeError("network down")
    client.log_out = _boom
    with _patch_user(user), store.patched(), _patch_creds(), _patch_client(client), \
            patch("chat.gemini_api.invalidate_user_sessions"):
        out = _run(auth_mod.telegram_disconnect(_FakeRequest()))
    assert out == {"success": True}
    assert store.blob is None


# ---------------------------------------------------------------------------
# popup page + router shape
# ---------------------------------------------------------------------------

def test_connect_page_shows_current_number_and_endpoints():
    user = {**USER, "service_credentials": {"telegram": {"oauth_blob": {"session": "s", "phone": "+447700900123"}}}}
    with _patch_user(user), _patch_creds():
        resp = _run(auth_mod.telegram_connect_page(_FakeRequest(), popup="1"))
    html = resp.body.decode()
    assert '"+••••••••0123"' in html
    assert "+447700900123" not in html  # only the masked form reaches the page
    for path in ("/auth/telegram/send-code", "/auth/telegram/verify", "/auth/telegram/2fa"):
        assert path in html
    assert "oauth_callback_success" in html


def test_connect_page_without_session():
    async def _none(_cookie):
        return None
    with patch.object(auth_mod, "get_user_from_cookie", _none):
        resp = _run(auth_mod.telegram_connect_page(_FakeRequest(cookies={}), popup="1"))
    assert b"Authentication Required" in resp.body


def test_connect_page_unconfigured():
    with _patch_user(), _patch_creds(configured=False):
        resp = _run(auth_mod.telegram_connect_page(_FakeRequest(), popup="1"))
    assert b"Telegram not configured" in resp.body


def test_every_route_lives_under_auth_telegram():
    paths = {route.path for route in auth_mod.router.routes}
    assert paths == {
        "/auth/telegram", "/auth/telegram/send-code", "/auth/telegram/verify",
        "/auth/telegram/2fa", "/auth/telegram/disconnect",
    }
    assert all(p == "/auth/telegram" or p.startswith("/auth/telegram/") for p in paths)
