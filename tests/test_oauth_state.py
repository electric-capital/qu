"""Tests for the shared signed OAuth state cookie (auth/oauth_state.py).

Every OAuth flow mints its CSRF nonce through this module, so the
properties pinned here -- signature, TTL, nonce comparison, session
binding, cookie flags -- cover the app login, Google Services and all
oauth-kind connector callbacks at once.  The login flow's use of it
(state issued with the sign-in URL, verified in the callback) is
covered at the end.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

import auth.google_login as google_login
import auth.oauth_state as oauth_state
from auth.oauth_state import (
    STATE_TTL_SECONDS,
    clear_oauth_state,
    mint_oauth_state,
    read_oauth_state,
    verify_oauth_state,
)

COOKIE = "unit_oauth_state"


@pytest.fixture(autouse=True)
def _fixed_secret_key():
    with patch.object(oauth_state, "get_secret_key", return_value="test-secret"):
        yield


class _FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies if cookies is not None else {}


def _request_with(issued) -> _FakeRequest:
    return _FakeRequest(cookies={issued.cookie_name: issued.cookie_value})


# ---------------------------------------------------------------------------
# mint / attach
# ---------------------------------------------------------------------------

def test_mint_sets_httponly_lax_cookie_with_ttl():
    issued = mint_oauth_state(COOKIE, user_id=3, popup=True)
    response = issued.attach(Response())
    header = response.headers["set-cookie"]
    assert header.startswith(f"{COOKIE}=")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert f"Max-Age={STATE_TTL_SECONDS}" in header


def test_mint_secure_flag_follows_session_cookie():
    with patch.object(oauth_state, "COOKIE_SECURE", True):
        header = mint_oauth_state(COOKIE).attach(Response()).headers["set-cookie"]
    assert "Secure" in header
    with patch.object(oauth_state, "COOKIE_SECURE", False):
        header = mint_oauth_state(COOKIE).attach(Response()).headers["set-cookie"]
    assert "Secure" not in header


def test_each_mint_is_a_fresh_nonce():
    assert mint_oauth_state(COOKIE).state != mint_oauth_state(COOKIE).state


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def test_verify_roundtrip_returns_payload_with_extras():
    issued = mint_oauth_state(
        COOKIE, user_id=3, popup=True, extra={"code_verifier": "v"},
    )
    payload = verify_oauth_state(_request_with(issued), COOKIE, issued.state, user_id=3)
    assert payload == {
        "csrf": issued.state, "uid": 3, "popup": True, "code_verifier": "v",
    }


def test_verify_rejects_nonce_mismatch_and_missing_state():
    issued = mint_oauth_state(COOKIE, user_id=3)
    request = _request_with(issued)
    assert verify_oauth_state(request, COOKIE, "other", user_id=3) is None
    assert verify_oauth_state(request, COOKIE, None, user_id=3) is None
    assert verify_oauth_state(request, COOKIE, "", user_id=3) is None


def test_verify_rejects_state_bound_to_another_user():
    issued = mint_oauth_state(COOKIE, user_id=3)
    request = _request_with(issued)
    assert verify_oauth_state(request, COOKIE, issued.state, user_id=4) is None
    # An unbound (login) state cannot complete a connector callback either.
    unbound = mint_oauth_state(COOKIE)
    assert verify_oauth_state(_request_with(unbound), COOKIE, unbound.state, user_id=3) is None
    # And a bound state cannot pass as the unbound login state.
    assert verify_oauth_state(request, COOKIE, issued.state) is None


def test_verify_rejects_missing_tampered_and_foreign_key_cookies():
    issued = mint_oauth_state(COOKIE, user_id=3)
    assert verify_oauth_state(_FakeRequest(), COOKIE, issued.state, user_id=3) is None

    tampered = issued.cookie_value[:-3] + "xyz"
    request = _FakeRequest(cookies={COOKIE: tampered})
    assert verify_oauth_state(request, COOKIE, issued.state, user_id=3) is None

    # Plaintext JSON (the pre-hardening format) never verifies.
    request = _FakeRequest(cookies={COOKIE: '{"csrf": "%s", "uid": 3}' % issued.state})
    assert verify_oauth_state(request, COOKIE, issued.state, user_id=3) is None

    # Signed under a different key / salt: not ours.
    foreign = URLSafeTimedSerializer("other", salt=oauth_state._SALT).dumps(
        {"csrf": issued.state, "uid": 3, "popup": False},
    )
    request = _FakeRequest(cookies={COOKIE: foreign})
    assert verify_oauth_state(request, COOKIE, issued.state, user_id=3) is None
    session_salted = URLSafeTimedSerializer("test-secret", salt="quest-session").dumps(
        {"csrf": issued.state, "uid": 3, "popup": False},
    )
    request = _FakeRequest(cookies={COOKIE: session_salted})
    assert verify_oauth_state(request, COOKIE, issued.state, user_id=3) is None


def test_verify_rejects_expired_state():
    issued = mint_oauth_state(COOKIE, user_id=3)
    request = _request_with(issued)
    real_loads = URLSafeTimedSerializer.loads

    def _expired_loads(self, value, max_age=None, **kw):
        # Pretend the cookie is older than the TTL.
        import time
        with patch("itsdangerous.timed.time.time", return_value=time.time() + STATE_TTL_SECONDS + 5):
            return real_loads(self, value, max_age=max_age, **kw)

    with patch.object(URLSafeTimedSerializer, "loads", _expired_loads):
        assert read_oauth_state(request, COOKIE) is None
        assert verify_oauth_state(request, COOKIE, issued.state, user_id=3) is None


def test_read_returns_payload_without_nonce_check():
    issued = mint_oauth_state(COOKIE, user_id=3, popup=True)
    assert read_oauth_state(_request_with(issued), COOKIE)["popup"] is True
    assert read_oauth_state(_FakeRequest(), COOKIE) is None


def test_clear_deletes_cookie():
    response = clear_oauth_state(Response(), COOKIE)
    assert f'{COOKIE}="";' in response.headers["set-cookie"]


# ---------------------------------------------------------------------------
# App login flow: state issued with the sign-in URL, enforced in callback
# ---------------------------------------------------------------------------

def _login_client() -> TestClient:
    app = FastAPI()
    app.include_router(google_login.router)
    return TestClient(app)


def _patch_login_flow():
    flow = MagicMock()

    def _authorization_url(**kwargs):
        return f"https://accounts.google.com/o/oauth2/auth?state={kwargs['state']}", kwargs["state"]

    flow.authorization_url = MagicMock(side_effect=_authorization_url)
    flow.fetch_token = MagicMock()
    return patch.object(google_login, "get_login_oauth_flow", return_value=flow)


def test_login_url_issues_signed_state_cookie_matching_url():
    client = _login_client()
    with _patch_login_flow():
        response = client.get("/auth/login-url")
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.json()["auth_url"]).query)["state"][0]
    cookie = response.cookies.get(google_login._STATE_COOKIE)
    assert cookie
    payload = read_oauth_state(
        _FakeRequest(cookies={google_login._STATE_COOKIE: cookie}),
        google_login._STATE_COOKIE,
    )
    assert payload == {"csrf": state, "uid": None, "popup": False}


def test_sign_in_page_issues_state_cookie():
    client = _login_client()
    with _patch_login_flow():
        response = client.get("/auth/")
    assert response.status_code == 200
    assert google_login._STATE_COOKIE in response.cookies
    assert b"Sign in with Google" in response.content


def test_login_callback_rejects_missing_state_before_token_exchange():
    client = _login_client()
    with _patch_login_flow() as flow_patch:
        response = client.get("/auth/callback?code=abc", follow_redirects=False)
    assert response.status_code == 200
    assert b"Security Error" in response.content
    flow_patch.return_value.fetch_token.assert_not_called()


def test_login_callback_rejects_mismatched_state_before_token_exchange():
    client = _login_client()
    with _patch_login_flow() as flow_patch:
        client.get("/auth/login-url")  # sets the state cookie
        response = client.get(
            "/auth/callback?code=abc&state=attacker", follow_redirects=False,
        )
    assert b"Security Error" in response.content
    flow_patch.return_value.fetch_token.assert_not_called()
    # The rejected state cookie is dropped.
    assert 'login_oauth_state="";' in response.headers.get("set-cookie", "")
