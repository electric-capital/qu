"""Tests for the UniFi plugin's key-entry router (plugins/unifi/connect.py).

No OAuth provider: ``POST /auth/unifi/keys`` tests each pasted key against
the controller and stores it in the credential row's ``oauth_blob``.
Coverage: verify-before-store, keep-on-omitted / remove-on-empty, the
at-least-one-key rule, rejection messages, disconnect, and the popup page.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import plugins.unifi.connect as connect_mod
from plugins.unifi.upstream import UnifiAuthError, UnifiError, unifi_connected


def _run(coro):
    return asyncio.run(coro)


USER = {"id": 7, "email": "u@example.com", "settings": {}}
_CONFIG = {"host": "https://unifi.test", "verify_tls": False}


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body
        self.cookies = cookies if cookies is not None else {connect_mod.COOKIE_NAME: "signed"}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _patch_user(user=USER):
    async def _fake(_cookie):
        return user
    return patch.object(connect_mod, "get_user_from_cookie", _fake)


class _Store:
    """In-memory stand-in for the user_service_credentials row."""

    def __init__(self, blob=None):
        self.blob = blob

    def patches(self):
        async def get_credential(user_id, service):
            assert service == "unifi"
            return {"oauth_blob": self.blob} if self.blob is not None else None

        async def upsert_credential(user_id, service, *, secret=None, oauth_blob=None):
            assert service == "unifi"
            self.blob = oauth_blob
            return {"oauth_blob": oauth_blob}

        async def delete_credential(user_id, service):
            self.blob = None
            return True

        return (
            patch.object(connect_mod, "get_credential", get_credential),
            patch.object(connect_mod, "upsert_credential", upsert_credential),
            patch.object(connect_mod, "delete_credential", delete_credential),
        )


@pytest.fixture()
def env():
    """Logged-in user, configured host, patched store + verifiers."""
    store = _Store()
    verify_network = AsyncMock(return_value={"applicationVersion": "9.3.45"})
    verify_protect = AsyncMock(return_value={"applicationVersion": "6.0.1"})
    patches = [
        _patch_user(),
        patch.object(connect_mod, "load_unifi_config", return_value=_CONFIG),
        patch.object(connect_mod, "verify_network_key", verify_network),
        patch.object(connect_mod, "verify_protect_key", verify_protect),
        patch("chat.gemini_api.invalidate_user_sessions"),
        *store.patches(),
    ]
    for p in patches:
        p.start()
    try:
        yield store, verify_network, verify_protect
    finally:
        for p in reversed(patches):
            p.stop()


def _body(resp) -> dict:
    if isinstance(resp, dict):
        return resp
    return json.loads(resp.body)


def _save(payload):
    return connect_mod.unifi_save_keys(_FakeRequest(payload))


# ---------------------------------------------------------------------------
# POST /auth/unifi/keys
# ---------------------------------------------------------------------------

class TestSaveKeys:
    def test_stores_both_keys_after_verifying_each(self, env):
        store, verify_network, verify_protect = env
        out = _body(_run(_save({"network_api_key": " net-key ", "protect_api_key": "prot-key"})))
        assert out["success"] is True
        assert out["network_key_masked"].endswith("-key") and out["protect_key_masked"].endswith("-key")
        assert out["network_version"] == "9.3.45" and out["protect_version"] == "6.0.1"
        verify_network.assert_awaited_once_with(_CONFIG, "net-key")
        verify_protect.assert_awaited_once_with(_CONFIG, "prot-key")
        assert store.blob["network_api_key"] == "net-key"
        assert store.blob["protect_api_key"] == "prot-key"
        assert store.blob["verified_at"]
        assert unifi_connected({"oauth_blob": store.blob})

    def test_single_key_is_enough(self, env):
        store, verify_network, verify_protect = env
        out = _body(_run(_save({"protect_api_key": "prot-key"})))
        assert out["success"] is True and out["network_key_masked"] is None
        verify_network.assert_not_awaited()
        assert "network_api_key" not in store.blob

    def test_omitted_field_keeps_stored_key(self, env):
        store, verify_network, verify_protect = env
        store.blob = {"network_api_key": "old-net", "network_version": "9.0.0"}
        out = _body(_run(_save({"protect_api_key": "prot-key"})))
        assert out["success"] is True
        verify_network.assert_not_awaited()
        assert store.blob["network_api_key"] == "old-net"
        assert store.blob["network_version"] == "9.0.0"
        assert store.blob["protect_api_key"] == "prot-key"

    def test_empty_string_removes_key(self, env):
        store, *_ = env
        store.blob = {"network_api_key": "old-net", "protect_api_key": "old-prot", "network_version": "9"}
        out = _body(_run(_save({"network_api_key": ""})))
        assert out["success"] is True
        assert "network_api_key" not in store.blob and "network_version" not in store.blob
        assert store.blob["protect_api_key"] == "old-prot"

    def test_removing_last_key_refused(self, env):
        store, *_ = env
        store.blob = {"network_api_key": "old-net"}
        resp = _run(_save({"network_api_key": ""}))
        assert resp.status_code == 400 and _body(resp)["error"] == "no_keys"
        assert store.blob == {"network_api_key": "old-net"}  # untouched

    def test_no_keys_at_all_refused(self, env):
        resp = _run(_save({}))
        assert resp.status_code == 400 and _body(resp)["error"] == "no_keys"

    def test_rejected_network_key_not_stored(self, env):
        store, verify_network, _ = env
        verify_network.side_effect = UnifiAuthError("nope", status_code=401)
        resp = _run(_save({"network_api_key": "bad", "protect_api_key": "prot-key"}))
        assert resp.status_code == 400
        assert _body(resp)["error"] == "invalid_network_api_key"
        assert "Control Plane" in _body(resp)["message"]
        assert store.blob is None

    def test_rejected_protect_key(self, env):
        store, _, verify_protect = env
        verify_protect.side_effect = UnifiAuthError("nope", status_code=403)
        resp = _run(_save({"protect_api_key": "bad"}))
        assert resp.status_code == 400 and _body(resp)["error"] == "invalid_protect_api_key"
        assert store.blob is None

    def test_unreachable_controller_is_502(self, env):
        store, verify_network, _ = env
        verify_network.side_effect = UnifiError("Could not connect", code="unifi_unreachable")
        resp = _run(_save({"network_api_key": "k"}))
        assert resp.status_code == 502 and _body(resp)["error"] == "unifi_unreachable"
        assert store.blob is None

    def test_malformed_keys(self, env):
        for payload in ({"network_api_key": 42}, {"network_api_key": "has space"},
                        {"protect_api_key": "x" * 600}):
            resp = _run(_save(payload))
            assert resp.status_code == 400 and _body(resp)["error"] == "invalid_api_key", payload

    def test_not_configured_is_503(self, env):
        with patch.object(
            connect_mod, "load_unifi_config",
            side_effect=HTTPException(status_code=500, detail="UniFi controller host not configured."),
        ):
            resp = _run(_save({"network_api_key": "k"}))
        assert resp.status_code == 503 and _body(resp)["error"] == "unifi_not_configured"

    def test_invalid_json_body(self, env):
        with pytest.raises(HTTPException) as exc:
            _run(connect_mod.unifi_save_keys(_FakeRequest(ValueError("bad"))))
        assert exc.value.status_code == 400

    def test_requires_login(self, env):
        with _patch_user(None):
            with pytest.raises(HTTPException) as exc:
                _run(_save({"network_api_key": "k"}))
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Disconnect + page
# ---------------------------------------------------------------------------

class TestDisconnectAndPage:
    def test_disconnect_deletes_row(self, env):
        store, *_ = env
        store.blob = {"network_api_key": "k"}
        out = _run(connect_mod.unifi_disconnect(_FakeRequest({})))
        assert out == {"success": True}
        assert store.blob is None

    def test_page_embeds_masked_state_only(self, env):
        store, *_ = env
        store.blob = {"network_api_key": "network-secret-1234", "network_version": "9.3.45"}
        resp = _run(connect_mod.unifi_connect_page(_FakeRequest(), popup="1"))
        page = resp.body.decode()
        assert "https://unifi.test" in page
        assert "network-secret-1234" not in page
        assert "1234" in page and "9.3.45" in page
        assert "/auth/unifi/keys" in page and "oauth_callback_success" in page

    def test_page_escapes_stored_version_for_script_context(self, env):
        """A hostile controller's applicationVersion must not break out of
        the inline <script> that embeds the popup state (#279224)."""
        store, *_ = env
        hostile = "9.3.45</script><img src=x onerror=alert(1)><script>"
        store.blob = {"network_api_key": "network-secret-1234", "network_version": hostile}
        resp = _run(connect_mod.unifi_connect_page(_FakeRequest(), popup="1"))
        page = resp.body.decode()
        # Exactly one script element: the state cannot terminate it early.
        assert page.count("</script>") == 1
        # No attacker markup reaches the HTML parser (only the escaped,
        # string-internal form remains, which is inert).
        assert "<img" not in page and "onerror=" not in page.split("var state = ", 1)[0]
        # The escaped form still decodes to the original runtime value.
        embedded = page.split("var state = ", 1)[1].split(";\n", 1)[0]
        assert "\\u003c" in embedded and "\\u003e" in embedded
        assert json.loads(embedded)["network_version"] == hostile

    def test_page_when_not_configured(self, env):
        with patch.object(
            connect_mod, "load_unifi_config",
            side_effect=HTTPException(status_code=500, detail="UniFi controller host not configured."),
        ):
            resp = _run(connect_mod.unifi_connect_page(_FakeRequest(), popup="1"))
        assert "not configured" in resp.body.decode()

    def test_page_when_logged_out(self, env):
        with _patch_user(None):
            resp = _run(connect_mod.unifi_connect_page(_FakeRequest(cookies={}), popup="1"))
        assert "Authentication Required" in resp.body.decode()
