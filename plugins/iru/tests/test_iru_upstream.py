"""Tests for plugins/iru/upstream.py: tenant URL normalization, the admin
validate hook, the per-user token helpers, and the HTTP client (auth /
permission / not-found / rate-limit / transport errors, paging helpers)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from plugins.iru.tests.fake_tenant import API_URL, DEVICES, TOKEN, FakeTenant
from plugins.iru.upstream import (
    API_PREFIX,
    IruAuthError,
    IruClient,
    IruError,
    cursor_from_next,
    get_user_api_token,
    iru_connected,
    iru_is_configured,
    is_uuid,
    normalize_api_url,
    page_items,
    validate_api_token,
    validate_iru_credentials,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Admin config
# ---------------------------------------------------------------------------

class TestNormalizeApiUrl:
    @pytest.mark.parametrize("raw, expected", [
        ("acme.api.iru.com", "https://acme.api.iru.com"),
        ("https://acme.api.iru.com/", "https://acme.api.iru.com"),
        ("https://acme.api.eu.iru.com/api/v1", "https://acme.api.eu.iru.com"),
        ("https://acme.api.eu.iru.com/api/v1/", "https://acme.api.eu.iru.com"),
        ("  ACME.api.kandji.io ", "https://acme.api.kandji.io"),
        ("https://acme.api.eu.kandji.io:443", "https://acme.api.eu.kandji.io"),
        ("https://my-tenant2.api.iru.com", "https://my-tenant2.api.iru.com"),
    ])
    def test_accepts_tenant_api_hosts(self, raw, expected):
        assert normalize_api_url(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", "   ",
        "http://acme.api.iru.com",            # plaintext
        "https://acme.kandji.io",             # the web-app host, not the API host
        "https://acme.iru.com",
        "https://api.iru.com",                # no tenant subdomain
        "https://acme.api.iru.com:8443",      # custom port
        "https://acme.api.iru.com/devices",   # a path
        "https://acme.api.iru.com/?x=1",
        "https://u:p@acme.api.iru.com",
        "https://evil.example/acme.api.iru.com",
        "https://acme.api.iru.com.evil.example",
        "https://-bad-.api.iru.com",
    ])
    def test_rejects_malformed_or_foreign_hosts(self, raw):
        with pytest.raises(ValueError):
            normalize_api_url(raw)

    def test_non_string_rejected(self):
        with pytest.raises(ValueError):
            normalize_api_url(None)


class TestAdminHooks:
    def test_validate_strips_and_normalizes(self):
        out = validate_iru_credentials({"api_url": "  acme.api.iru.com/api/v1 "})
        assert out == {"api_url": "https://acme.api.iru.com"}

    def test_validate_rejects_bad_url(self):
        with pytest.raises(ValueError, match="API host"):
            validate_iru_credentials({"api_url": "https://acme.kandji.io"})

    def test_validate_passes_through_empty(self):
        assert validate_iru_credentials({"api_url": ""}) == {"api_url": ""}

    def test_is_configured(self):
        assert iru_is_configured({"api_url": API_URL})
        assert not iru_is_configured({})
        assert not iru_is_configured({"api_url": ""})


# ---------------------------------------------------------------------------
# Per-user token
# ---------------------------------------------------------------------------

class TestTokenHelpers:
    @pytest.mark.parametrize("token", [TOKEN, "a" * 16, "x" * 256])
    def test_validate_accepts(self, token):
        assert validate_api_token(token) is None

    @pytest.mark.parametrize("token, fragment", [
        ("short", "too short"),
        ("has a space in it here", "spaces"),
        ("has\nnewline-0123456789", "spaces"),
        ("x" * 257, "too long"),
    ])
    def test_validate_rejects(self, token, fragment):
        assert fragment in validate_api_token(token)

    def test_get_user_api_token(self):
        assert get_user_api_token({"service_credentials": {"iru": {"secret": TOKEN}}}) == TOKEN
        assert get_user_api_token({"service_credentials": {"iru": {"secret": "  "}}}) is None
        assert get_user_api_token({"service_credentials": {}}) is None
        assert get_user_api_token({}) is None

    def test_connected_hook(self):
        assert iru_connected({"secret": TOKEN})
        assert not iru_connected({"secret": ""})
        assert not iru_connected({"secret": None})
        assert not iru_connected({"oauth_blob": {"x": 1}})

    def test_is_uuid(self):
        assert is_uuid(DEVICES[0]["device_id"])
        assert not is_uuid("C02AAA111111")
        assert not is_uuid(None)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _client(tenant: FakeTenant, token: str = TOKEN) -> IruClient:
    return IruClient(API_URL, token, transport=tenant.transport())


class TestClient:
    def test_sends_bearer_and_parses_json(self):
        tenant = FakeTenant()
        rows = _run(_client(tenant).get_json(f"{API_PREFIX}/devices", params={"limit": 2}))
        assert [r["device_name"] for r in rows] == [DEVICES[0]["device_name"], DEVICES[1]["device_name"]]
        assert tenant.calls == [("GET", "/api/v1/devices")]
        assert tenant.last_params["/api/v1/devices"] == {"limit": "2"}

    def test_none_params_dropped(self):
        tenant = FakeTenant()
        _run(_client(tenant).get_json(f"{API_PREFIX}/devices", params={"platform": None, "limit": 1}))
        assert tenant.last_params["/api/v1/devices"] == {"limit": "1"}

    def test_401_is_auth_error(self):
        tenant = FakeTenant()
        with pytest.raises(IruAuthError) as info:
            _run(_client(tenant, token="stale").get_json(f"{API_PREFIX}/devices"))
        assert info.value.code == "iru_auth_failed"
        assert info.value.status_code == 401
        assert "Re-enter" in str(info.value)

    def test_403_names_missing_permission(self):
        tenant = FakeTenant(forbidden_paths={"/api/v1/settings/licensing"})
        with pytest.raises(IruAuthError) as info:
            _run(_client(tenant).get_json(f"{API_PREFIX}/settings/licensing"))
        assert info.value.status_code == 403
        assert "permission" in str(info.value)

    def test_404_maps_to_not_found(self):
        tenant = FakeTenant()
        with pytest.raises(IruError) as info:
            _run(_client(tenant).get_json(f"{API_PREFIX}/devices/00000000-0000-0000-0000-000000000000"))
        assert info.value.code == "iru_not_found"
        assert info.value.status_code == 404

    def test_429_carries_retry_after(self):
        tenant = FakeTenant(rate_limited=True)
        with pytest.raises(IruError) as info:
            _run(_client(tenant).get_json(f"{API_PREFIX}/devices"))
        assert info.value.code == "iru_rate_limited"
        assert info.value.retry_after == 42
        assert "10,000" in str(info.value)

    def test_other_4xx_5xx(self):
        def handler(request):
            return httpx.Response(500, json={"detail": "boom"})
        client = IruClient(API_URL, TOKEN, transport=httpx.MockTransport(handler))
        with pytest.raises(IruError) as info:
            _run(client.get_json("/api/v1/devices"))
        assert info.value.code == "iru_request_failed"
        assert info.value.status_code == 500
        assert "boom" in str(info.value)

    def test_error_message_shapes(self):
        payloads = [
            (httpx.Response(400, json={"error": "bad thing"}), "bad thing"),
            (httpx.Response(400, json=["first problem"]), "first problem"),
            (httpx.Response(400, text="plain text failure"), "plain text failure"),
            (httpx.Response(400, json={"detail": {"field": ["required"]}}), "required"),
        ]
        for resp, fragment in payloads:
            client = IruClient(API_URL, TOKEN, transport=httpx.MockTransport(lambda r, resp=resp: resp))
            with pytest.raises(IruError) as info:
                _run(client.get_json("/api/v1/x"))
            assert fragment in str(info.value)

    def test_connect_error(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)
        client = IruClient(API_URL, TOKEN, transport=httpx.MockTransport(handler))
        with pytest.raises(IruError) as info:
            _run(client.get_json("/api/v1/devices"))
        assert info.value.code == "iru_unreachable"

    def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)
        client = IruClient(API_URL, TOKEN, transport=httpx.MockTransport(handler))
        with pytest.raises(IruError) as info:
            _run(client.get_json("/api/v1/devices"))
        assert info.value.code == "iru_timeout"

    def test_non_json_body(self):
        client = IruClient(API_URL, TOKEN, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text="<html>")))
        with pytest.raises(IruError, match="non-JSON"):
            _run(client.get_json("/api/v1/devices"))

    def test_empty_body_is_none(self):
        client = IruClient(API_URL, TOKEN, transport=httpx.MockTransport(
            lambda r: httpx.Response(204)))
        assert _run(client.get_json("/api/v1/devices")) is None

    def test_context_manager_closes(self):
        tenant = FakeTenant()

        async def go():
            async with _client(tenant) as client:
                await client.get_json(f"{API_PREFIX}/tags", params={"search": ""})
                assert client._client is not None
            assert client._client is None
        _run(go())


class TestPaging:
    def test_get_all_offset_pages_bare_list(self):
        tenant = FakeTenant()
        rows = _run(_client(tenant).get_all_offset_pages(f"{API_PREFIX}/devices", page_limit=2))
        assert [r["serial_number"] for r in rows] == [d["serial_number"] for d in DEVICES]
        assert [c[1] for c in tenant.calls] == ["/api/v1/devices", "/api/v1/devices"]

    def test_get_all_offset_pages_follows_next_when_server_caps_pages(self):
        # The fake caps status pages at 2 rows even though 300 were asked for.
        tenant = FakeTenant(status_page_size=2)
        from plugins.iru.tests.fake_tenant import LIBRARY_ITEM_ZOOM
        rows = _run(_client(tenant).get_all_offset_pages(
            f"{API_PREFIX}/library/library-items/{LIBRARY_ITEM_ZOOM}/status"))
        assert [r["status"] for r in rows] == ["PASS", "ERROR", "PENDING"]
        assert len(tenant.calls) == 2

    def test_get_all_offset_pages_results_shape_and_cap(self):
        tenant = FakeTenant()
        rows = _run(_client(tenant).get_all_offset_pages(
            f"{API_PREFIX}/blueprints", page_limit=1, max_items=1))
        assert len(rows) == 1
        assert len(tenant.calls) == 1

    def test_page_items_shapes(self):
        assert page_items([1, 2]) == [1, 2]
        assert page_items({"results": [1]}) == [1]
        assert page_items({"cursor": None, "data": [2]}) == [2]
        with pytest.raises(IruError, match="no row list"):
            page_items({"count": 3}, "/x")
        with pytest.raises(IruError):
            page_items("nope")

    def test_cursor_from_next(self):
        assert cursor_from_next({"next": f"{API_URL}/api/v1/users?cursor=cD0yOTE0Mw%3D%3D"}) == "cD0yOTE0Mw=="
        assert cursor_from_next({"next": None}) is None
        assert cursor_from_next({"next": f"{API_URL}/api/v1/users"}) is None
        assert cursor_from_next([1]) is None
