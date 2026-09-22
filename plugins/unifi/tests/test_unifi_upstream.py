"""Tests for plugins/unifi/upstream.py: host normalization, the admin
validate hook, the per-user key helpers, and the HTTP client (auth /
not-found / transport errors, paging)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from plugins.unifi.tests.fake_controller import (
    CLIENTS,
    HOST,
    NETWORK_KEY,
    PROTECT_KEY,
    FakeController,
)
from plugins.unifi.upstream import (
    NETWORK_API_PREFIX,
    UnifiAuthError,
    UnifiClient,
    UnifiError,
    get_network_api_key,
    get_protect_api_key,
    mask_key,
    normalize_host,
    unifi_connected,
    unifi_is_configured,
    validate_unifi_credentials,
    verify_network_key,
    verify_protect_key,
    verify_tls_enabled,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Admin config
# ---------------------------------------------------------------------------

class TestNormalizeHost:
    @pytest.mark.parametrize("raw, expected", [
        ("192.168.1.1", "https://192.168.1.1"),
        ("  unifi.local ", "https://unifi.local"),
        ("https://unifi.local/", "https://unifi.local"),
        ("https://unifi.local:8443", "https://unifi.local:8443"),
        ("https://unifi.local:443", "https://unifi.local"),
        ("HTTPS://UniFi.Local", "https://unifi.local"),
        ("[fd00::1]", "https://[fd00::1]"),
    ])
    def test_accepts_bare_hosts_and_https_origins(self, raw, expected):
        assert normalize_host(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", "   ", "http://unifi.local", "https://unifi.local/proxy/network",
        "https://unifi.local/?x=1", "https://user:pw@unifi.local", "https://",
        "https://unifi.local:notaport", "ftp://unifi.local", "bad host!",
    ])
    def test_rejects_paths_credentials_and_plain_http(self, raw):
        with pytest.raises(ValueError):
            normalize_host(raw)

    def test_non_string_rejected(self):
        with pytest.raises(ValueError):
            normalize_host(None)


class TestValidateHook:
    def test_normalizes_host_and_strips(self):
        out = validate_unifi_credentials({"host": " 10.0.0.1 ", "verify_tls": True})
        assert out == {"host": "https://10.0.0.1", "verify_tls": True}

    def test_bad_host_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_unifi_credentials({"host": "http://10.0.0.1"})

    def test_missing_host_left_to_required_check(self):
        assert validate_unifi_credentials({"verify_tls": False}) == {"verify_tls": False}

    def test_is_configured_and_verify_tls(self):
        assert unifi_is_configured({"host": "https://x"})
        assert not unifi_is_configured({})
        assert verify_tls_enabled({"verify_tls": True})
        assert not verify_tls_enabled({"verify_tls": "true"})
        assert not verify_tls_enabled(None)


# ---------------------------------------------------------------------------
# Per-user keys
# ---------------------------------------------------------------------------

class TestUserKeys:
    def test_keys_read_from_service_credentials_row(self):
        user = {"service_credentials": {"unifi": {"oauth_blob": {
            "network_api_key": "n", "protect_api_key": "p",
        }}}}
        assert get_network_api_key(user) == "n"
        assert get_protect_api_key(user) == "p"

    def test_missing_row_or_blank_key(self):
        assert get_network_api_key({}) is None
        user = {"service_credentials": {"unifi": {"oauth_blob": {"network_api_key": "  "}}}}
        assert get_network_api_key(user) is None
        assert get_protect_api_key(user) is None

    def test_connected_requires_at_least_one_key(self):
        assert unifi_connected({"oauth_blob": {"network_api_key": "n"}})
        assert unifi_connected({"oauth_blob": {"protect_api_key": "p"}})
        assert not unifi_connected({"oauth_blob": {}})
        assert not unifi_connected({"oauth_blob": None})
        assert not unifi_connected({"secret": "legacy"})

    def test_mask_key(self):
        assert mask_key("abcdefgh") == "••••efgh"
        assert mask_key("ab") == "ab"
        assert mask_key(None) == ""


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _client(controller: FakeController, key: str = NETWORK_KEY) -> UnifiClient:
    return UnifiClient(HOST, key, transport=controller.transport())


class TestClient:
    def test_sends_api_key_header_and_parses_json(self):
        controller = FakeController()

        async def go():
            async with _client(controller) as client:
                return await client.get_json(f"{NETWORK_API_PREFIX}/info")

        assert _run(go()) == {"applicationVersion": "9.3.45"}
        assert controller.calls == [("GET", f"{NETWORK_API_PREFIX}/info")]

    def test_401_becomes_auth_error_with_upstream_message(self):
        controller = FakeController()

        async def go():
            async with _client(controller, "wrong") as client:
                await client.get_json(f"{NETWORK_API_PREFIX}/info")

        with pytest.raises(UnifiAuthError) as exc:
            _run(go())
        assert exc.value.status_code == 401
        assert exc.value.code == "unifi_auth_failed"
        assert "Invalid API key" in str(exc.value)

    def test_404_and_other_errors(self):
        controller = FakeController()

        async def go(path):
            async with _client(controller) as client:
                await client.get_json(path)

        with pytest.raises(UnifiError) as exc:
            _run(go(f"{NETWORK_API_PREFIX}/sites/site-1/devices/nope"))
        assert exc.value.code == "unifi_not_found"

    def test_transport_errors_are_wrapped(self):
        def boom(request):
            raise httpx.ConnectError("refused", request=request)

        async def go():
            async with UnifiClient(HOST, "k", transport=httpx.MockTransport(boom)) as client:
                await client.get_json("/x")

        with pytest.raises(UnifiError) as exc:
            _run(go())
        assert exc.value.code == "unifi_unreachable"
        assert HOST in str(exc.value)

    def test_timeout_wrapped(self):
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        async def go():
            async with UnifiClient(HOST, "k", transport=httpx.MockTransport(slow)) as client:
                await client.get_json("/x")

        with pytest.raises(UnifiError) as exc:
            _run(go())
        assert exc.value.code == "unifi_timeout"

    def test_get_paged_walks_every_page(self):
        controller = FakeController(client_page_size=2)

        async def go():
            async with _client(controller) as client:
                return await client.get_paged(f"{NETWORK_API_PREFIX}/sites/site-1/clients")

        rows = _run(go())
        assert [r["id"] for r in rows] == [c["id"] for c in CLIENTS]
        paged_calls = [c for c in controller.calls if c[1].endswith("/clients")]
        assert len(paged_calls) == 2

    def test_get_paged_respects_max_items(self):
        controller = FakeController(client_page_size=2)

        async def go():
            async with _client(controller) as client:
                return await client.get_paged(
                    f"{NETWORK_API_PREFIX}/sites/site-1/clients", max_items=2,
                )

        assert len(_run(go())) == 2
        assert len([c for c in controller.calls if c[1].endswith("/clients")]) == 1

    def test_get_paged_tolerates_bare_list(self):
        def handler(request):
            return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

        async def go():
            async with UnifiClient(HOST, "k", transport=httpx.MockTransport(handler)) as client:
                return await client.get_paged("/list")

        assert _run(go()) == [{"id": 1}, {"id": 2}]

    def test_get_bytes_returns_content_type(self):
        controller = FakeController()

        async def go():
            async with _client(controller, PROTECT_KEY) as client:
                return await client.get_bytes(
                    "/proxy/protect/integration/v1/cameras/cam-1/snapshot",
                )

        data, content_type = _run(go())
        assert data.startswith(b"\xff\xd8") and content_type == "image/jpeg"

    def test_verify_helpers(self):
        controller = FakeController()
        config = {"host": HOST}
        assert _run(verify_network_key(config, NETWORK_KEY, transport=controller.transport())) == {
            "applicationVersion": "9.3.45",
        }
        assert _run(verify_protect_key(config, PROTECT_KEY, transport=controller.transport())) == {
            "applicationVersion": "6.0.1",
        }
        with pytest.raises(UnifiAuthError):
            _run(verify_protect_key(config, NETWORK_KEY, transport=controller.transport()))
