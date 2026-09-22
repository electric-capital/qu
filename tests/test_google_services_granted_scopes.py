"""Tests for the Google Services OAuth callback persisting ACTUALLY-GRANTED scopes.

The callback (``auth/google_services.py:auth_google_services_callback``) must
store ``credentials.granted_scopes`` -- the scopes Google returned in the token
response's ``scope`` field -- rather than the scopes we *requested*
(``GOOGLE_SERVICE_SCOPES``). Storing the requested set verbatim would mask a
partial grant and leave the ``/connectors`` ``needs_reauth`` check reading clean
even when a needed scope (e.g. ``cloud-platform`` for GCP) was not
granted, producing 403 ``ACCESS_TOKEN_SCOPE_INSUFFICIENT`` at the API.

These tests exercise the callback with a mocked OAuth ``Flow`` (no network) and
assert the persisted ``scopes`` reflect what Google granted, and that the
``/connectors`` subset check correctly flips ``needs_reauth`` for a partial grant.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from auth.config import GOOGLE_SERVICE_SCOPES


def _run(coro):
    return asyncio.run(coro)


STATE = "st4te"


def _make_request(code="auth-code", cookie="signed-cookie"):
    req = MagicMock()
    req.cookies = {}
    # COOKIE_NAME is resolved at import time in auth.config; read it dynamically.
    from auth.config import COOKIE_NAME
    import auth.oauth_state as oauth_state
    req.cookies[COOKIE_NAME] = cookie
    # Signed state cookie bound to the fake session user (see
    # _patch_callback_deps), as the start route would have minted it.
    with patch.object(oauth_state, "get_secret_key", return_value="test-secret"):
        req.cookies["google_services_oauth_state"] = oauth_state._serializer().dumps(
            {"csrf": STATE, "uid": "u1", "popup": False},
        )
    req.base_url = "https://app.example.com/"
    req.query_params = {}
    return req


def _make_flow(granted_scopes, refresh_token="refresh-xyz", token="ya29.access"):
    """Build a mock Flow whose .credentials mirrors google-auth's shape."""
    creds = MagicMock()
    creds.token = token
    creds.refresh_token = refresh_token
    creds.expiry = datetime(2030, 1, 1, 0, 0, 0)
    creds.granted_scopes = granted_scopes
    flow = MagicMock()
    flow.fetch_token = MagicMock(return_value=None)
    flow.credentials = creds
    return flow


def _patch_callback_deps(
    flow, captured, email="user@example.com",
    userinfo_status=200, userinfo_body=None,
):
    """Patch out the callback's external dependencies.

    Returns a context manager stack via contextlib.ExitStack-like list of
    patches applied by the caller.  ``userinfo_status``/``userinfo_body``
    shape the mocked UserInfo response (defaults: 200 with the session
    email, i.e. the happy path).
    """
    import auth.google_services as gs
    import auth.oauth_state as oauth_state

    async def fake_get_user_from_cookie(_cookie):
        return {"id": "u1", "email": email}

    async def fake_update_user_field(_email, **fields):
        captured.update(fields)

    # userinfo check inside the callback verifies the services email matches the
    # session email -- mock the httpx client to return the same email.
    userinfo_resp = MagicMock()
    userinfo_resp.status_code = userinfo_status
    userinfo_resp.json = MagicMock(
        return_value={"email": email} if userinfo_body is None else userinfo_body
    )

    async def fake_get(url, headers=None):
        return userinfo_resp

    client_mock = MagicMock()
    client_mock.get = AsyncMock(side_effect=fake_get)
    async_ctx = MagicMock()
    async_ctx.__aenter__ = AsyncMock(return_value=client_mock)
    async_ctx.__aexit__ = AsyncMock(return_value=None)

    return [
        patch.object(gs, "get_user_from_cookie", new=AsyncMock(side_effect=fake_get_user_from_cookie)),
        patch.object(oauth_state, "get_secret_key", return_value="test-secret"),
        patch.object(gs, "get_google_services_oauth_flow", return_value=flow),
        patch.object(gs, "update_user_field", new=AsyncMock(side_effect=fake_update_user_field)),
        patch.object(gs.httpx, "AsyncClient", return_value=async_ctx),
        patch("chat.gemini_api.invalidate_user_sessions", lambda _uid: None),
    ]


def _invoke_callback(flow, captured, **dep_kwargs):
    from auth.google_services import auth_google_services_callback
    patches = _patch_callback_deps(flow, captured, **dep_kwargs)
    for p in patches:
        p.start()
    try:
        return _run(auth_google_services_callback(
            _make_request(), code="auth-code", state=STATE,
        ))
    finally:
        for p in patches:
            p.stop()


class TestGrantedScopePersistence:
    def test_stores_granted_scopes_full_grant(self):
        captured = {}
        flow = _make_flow(granted_scopes=list(GOOGLE_SERVICE_SCOPES))
        _invoke_callback(flow, captured)
        stored = captured["google_services_oauth"]["scopes"]
        assert set(stored) == set(GOOGLE_SERVICE_SCOPES)

    def test_stores_partial_grant_not_requested_set(self):
        """If Google grants a subset, we persist the subset (not the request)."""
        partial = [s for s in GOOGLE_SERVICE_SCOPES
                   if s != "https://www.googleapis.com/auth/cloud-platform"]
        captured = {}
        flow = _make_flow(granted_scopes=partial)
        _invoke_callback(flow, captured)
        stored = captured["google_services_oauth"]["scopes"]
        assert set(stored) == set(partial)
        # The GCP scope was NOT granted, so it must be absent from storage.
        assert "https://www.googleapis.com/auth/cloud-platform" not in stored

    def test_falls_back_to_requested_when_no_granted_scopes(self):
        """If Google returns no scope field, fall back to the requested set."""
        captured = {}
        flow = _make_flow(granted_scopes=None)
        _invoke_callback(flow, captured)
        stored = captured["google_services_oauth"]["scopes"]
        assert set(stored) == set(GOOGLE_SERVICE_SCOPES)


class TestNeedsReauthSubsetCheck:
    """The /connectors needs_reauth logic mirrored as a pure subset check.

    This documents/guards the behavior in chat/routes/user.py:get_connectors:
    needs_reauth is True iff the required (requested) scopes are NOT a subset of
    the stored (granted) scopes.
    """

    @staticmethod
    def _needs_reauth(stored_scopes):
        required = set(GOOGLE_SERVICE_SCOPES)
        return not required.issubset(set(stored_scopes))

    def test_full_grant_no_reauth(self):
        assert self._needs_reauth(list(GOOGLE_SERVICE_SCOPES)) is False

    def test_partial_grant_triggers_reauth(self):
        partial = [s for s in GOOGLE_SERVICE_SCOPES
                   if s != "https://www.googleapis.com/auth/cloud-platform"]
        assert self._needs_reauth(partial) is True


class TestUserInfoIdentityCheckFailsClosed:
    """The callback must never persist a token whose Google account it
    could not verify against the Quest session (finding #279228).

    Before the fix a non-200 UserInfo response skipped the same-email
    check entirely and stored the token anyway.
    """

    def test_non_200_userinfo_does_not_persist_token(self):
        captured = {}
        flow = _make_flow(granted_scopes=list(GOOGLE_SERVICE_SCOPES))
        resp = _invoke_callback(
            flow, captured,
            userinfo_status=403, userinfo_body={"error": "insufficient_scope"},
        )
        assert "google_services_oauth" not in captured
        assert "Failed to verify the Google account" in resp.body.decode()

    def test_missing_email_in_userinfo_does_not_persist_token(self):
        captured = {}
        flow = _make_flow(granted_scopes=list(GOOGLE_SERVICE_SCOPES))
        resp = _invoke_callback(
            flow, captured, userinfo_status=200, userinfo_body={"id": "123"},
        )
        assert "google_services_oauth" not in captured
        assert "Account Mismatch" in resp.body.decode()

    def test_different_account_does_not_persist_token(self):
        captured = {}
        flow = _make_flow(granted_scopes=list(GOOGLE_SERVICE_SCOPES))
        resp = _invoke_callback(
            flow, captured,
            userinfo_status=200, userinfo_body={"email": "other@example.com"},
        )
        assert "google_services_oauth" not in captured
        assert "Account Mismatch" in resp.body.decode()

    def test_email_comparison_is_case_insensitive(self):
        captured = {}
        flow = _make_flow(granted_scopes=list(GOOGLE_SERVICE_SCOPES))
        _invoke_callback(
            flow, captured,
            userinfo_status=200, userinfo_body={"email": "User@Example.com"},
        )
        assert captured["google_services_oauth"]["access_token"] == "ya29.access"


class TestServicesFlowRequestsIdentityScope:
    """The services flow must request an email identity scope so the
    UserInfo verification can actually succeed, without widening
    GOOGLE_SERVICE_SCOPES (which would flip needs_reauth for everyone)."""

    def test_flow_scopes_include_userinfo_email(self):
        import auth.google_credentials as gc
        from auth.google_credentials import (
            GOOGLE_SERVICES_IDENTITY_SCOPE, get_google_services_oauth_flow,
        )
        captured = {}

        def fake_from_client_config(client_config, scopes, redirect_uri):
            captured["scopes"] = list(scopes)
            return MagicMock()

        with patch.object(gc, "load_google_oauth_config", return_value={"web": {}}), \
             patch.object(gc.Flow, "from_client_config", side_effect=fake_from_client_config):
            get_google_services_oauth_flow("https://app.example.com/auth/google-services/callback")

        assert GOOGLE_SERVICES_IDENTITY_SCOPE in captured["scopes"]
        assert set(GOOGLE_SERVICE_SCOPES) <= set(captured["scopes"])
        assert GOOGLE_SERVICES_IDENTITY_SCOPE not in GOOGLE_SERVICE_SCOPES

    def test_flow_scopes_include_openid(self):
        """Google silently adds "openid" to the granted scopes whenever a
        userinfo.* scope is requested; google-auth-oauthlib then raises
        "Scope has changed" on fetch_token unless we requested it too."""
        import auth.google_credentials as gc
        from auth.google_credentials import get_google_services_oauth_flow
        captured = {}

        def fake_from_client_config(client_config, scopes, redirect_uri):
            captured["scopes"] = list(scopes)
            return MagicMock()

        with patch.object(gc, "load_google_oauth_config", return_value={"web": {}}), \
             patch.object(gc.Flow, "from_client_config", side_effect=fake_from_client_config):
            get_google_services_oauth_flow("https://app.example.com/auth/google-services/callback")

        assert "openid" in captured["scopes"]
        assert "openid" not in GOOGLE_SERVICE_SCOPES
