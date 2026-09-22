"""Every request-authenticating dependency enforces the admission policy.

Security finding #279231: session cookies never expire on their own and
``users.api_key`` is long-lived, so a user removed from the allowed login
domain / ``allowed_login_emails`` whitelist keeps working credentials.
Previously only ``get_current_user_cookie_or_apikey_checked`` applied
``check_user_allowed``; the script tool-call bridge, the authed-get/post
proxy, the Gmail Simple routes and a couple of root endpoints declared the
unchecked ``auth.session`` / ``chat.auth`` dependencies and kept serving
offboarded users. Now every dependency funnels through
``require_user_allowed``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import auth.session as session_auth
import chat.auth as chat_auth
from auth.config import ALLOWED_DOMAIN, COOKIE_NAME


ALLOWED_EMAIL = f"still-here@{ALLOWED_DOMAIN}"
OFFBOARDED_EMAIL = f"gone@not-{ALLOWED_DOMAIN}"


def _run(coro):
    return asyncio.run(coro)


class _Req:
    def __init__(self, headers: dict | None = None, cookies: dict | None = None):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.state = SimpleNamespace()
        self.url = SimpleNamespace(path="/api/tool-call")


def _cookie_req():
    return _Req(cookies={COOKIE_NAME: "signed"})


def _bearer_req():
    return _Req(headers={"Authorization": "Bearer users-api-key"})


@pytest.fixture()
def _resolve_user(monkeypatch):
    """Make every credential resolve to the user with the given email."""

    def _install(email: str):
        async def _from_cookie(_cookie):
            return {"id": 7, "email": email, "api_key": "users-api-key"}

        async def _by_api_key(_key):
            return {"id": 7, "email": email, "api_key": "users-api-key"}

        # Both modules bound the names at import time.
        monkeypatch.setattr(session_auth, "get_user_from_cookie", _from_cookie)
        monkeypatch.setattr(session_auth, "get_user_by_api_key", _by_api_key)
        monkeypatch.setattr(chat_auth, "get_user_from_cookie", _from_cookie)
        monkeypatch.setattr(chat_auth, "get_user_by_api_key", _by_api_key)

    return _install


DUAL_DEPS = (
    session_auth.get_current_user_cookie_or_apikey,
    chat_auth.get_current_user_cookie_or_apikey,
    chat_auth.get_current_user_cookie_or_apikey_checked,
)


@pytest.mark.parametrize("env", ["staging", "prod"])
class TestOffboardedUserRejected:
    def test_dual_auth_cookie_403(self, monkeypatch, _resolve_user, env):
        monkeypatch.setenv("QUEST_ENV", env)
        _resolve_user(OFFBOARDED_EMAIL)
        for dep in DUAL_DEPS:
            with pytest.raises(HTTPException) as exc:
                _run(dep(_cookie_req()))
            assert exc.value.status_code == 403, dep.__name__
            assert exc.value.detail["error"] == "access_denied"

    def test_dual_auth_bearer_403(self, monkeypatch, _resolve_user, env):
        monkeypatch.setenv("QUEST_ENV", env)
        _resolve_user(OFFBOARDED_EMAIL)
        for dep in DUAL_DEPS:
            with pytest.raises(HTTPException) as exc:
                _run(dep(_bearer_req()))
            assert exc.value.status_code == 403, dep.__name__
            assert exc.value.detail["error"] == "access_denied"

    def test_api_key_only_dependency_403(self, monkeypatch, _resolve_user, env):
        monkeypatch.setenv("QUEST_ENV", env)
        _resolve_user(OFFBOARDED_EMAIL)
        with pytest.raises(HTTPException) as exc:
            _run(session_auth.get_current_user(_bearer_req()))
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "access_denied"

    def test_cookie_does_not_fall_through_to_bearer(self, monkeypatch, _resolve_user, env):
        """A disallowed cookie user is rejected outright, not retried via the key."""
        monkeypatch.setenv("QUEST_ENV", env)
        _resolve_user(OFFBOARDED_EMAIL)
        seen_keys = []

        async def _by_api_key(key):
            seen_keys.append(key)
            return None

        monkeypatch.setattr(session_auth, "get_user_by_api_key", _by_api_key)
        monkeypatch.setattr(chat_auth, "get_user_by_api_key", _by_api_key)
        req = _Req(
            headers={"Authorization": "Bearer users-api-key"},
            cookies={COOKIE_NAME: "signed"},
        )
        for dep in DUAL_DEPS:
            with pytest.raises(HTTPException) as exc:
                _run(dep(req))
            assert exc.value.status_code == 403
        assert seen_keys == []


class TestAllowedUserStillPasses:
    @pytest.mark.parametrize("env", ["staging", "prod"])
    def test_allowed_domain_passes_every_dependency(self, monkeypatch, _resolve_user, env):
        monkeypatch.setenv("QUEST_ENV", env)
        _resolve_user(ALLOWED_EMAIL)
        for dep in DUAL_DEPS:
            assert _run(dep(_cookie_req()))["email"] == ALLOWED_EMAIL
            assert _run(dep(_bearer_req()))["email"] == ALLOWED_EMAIL
        assert _run(session_auth.get_current_user(_bearer_req()))["email"] == ALLOWED_EMAIL

    def test_local_mode_relaxes_the_check(self, monkeypatch, _resolve_user):
        monkeypatch.setenv("QUEST_ENV", "local")
        _resolve_user(OFFBOARDED_EMAIL)
        for dep in DUAL_DEPS:
            assert _run(dep(_cookie_req()))["email"] == OFFBOARDED_EMAIL
        assert _run(session_auth.get_current_user(_bearer_req()))["email"] == OFFBOARDED_EMAIL

    def test_unauthenticated_is_still_401(self, monkeypatch):
        monkeypatch.setenv("QUEST_ENV", "prod")
        for dep in DUAL_DEPS + (session_auth.get_current_user,):
            with pytest.raises(HTTPException) as exc:
                _run(dep(_Req()))
            assert exc.value.status_code == 401
