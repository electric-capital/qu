"""UniFi upstream access helpers (plugin module).

Server-side configuration (the admin ``unifi`` credential-store entry: the
controller host and the TLS-verification switch) and the per-user API keys
(a ``user_service_credentials`` row with ``oauth_blob`` JSON written by the
key-entry popup in plugins/unifi/connect.py) both resolve here, so the
connect router and every tool share one implementation.

The plugin talks DIRECTLY to a UniFi OS console (UDM / UCG / UNVR / Cloud
Key / self-hosted Network application) on the local network -- never via
unifi.ui.com. Two official local "Integration" APIs are used, each with its
own API key created inside the respective application (Settings > Control
Plane > Integrations):

- Network:  ``https://<host>/proxy/network/integration/v1/...``
- Protect:  ``https://<host>/proxy/protect/integration/v1/...``

Both authenticate with an ``X-API-KEY`` header. UniFi consoles ship a
self-signed certificate, so certificate verification is OFF unless the
admin turns it on (``verify_tls``).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

SERVICE_ID = "unifi"

NETWORK_API_PREFIX = "/proxy/network/integration/v1"
NETWORK_LEGACY_PREFIX = "/proxy/network/api"
PROTECT_API_PREFIX = "/proxy/protect/integration/v1"

# The Integration APIs page with offset/limit; 200 is the documented max.
PAGE_LIMIT = 200
# Hard cap on items collected across pages by one tool call (a very large
# site would otherwise produce an enormous tool result).
MAX_COLLECTED_ITEMS = 2000

_HTTP_TIMEOUT = 30.0
# Camera snapshots can take a few seconds on a busy NVR.
_SNAPSHOT_TIMEOUT = 45.0

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.\-]+$")

MISSING_KEYS_ERROR = (
    "UniFi is not connected. Add your Network and/or Protect API keys in "
    "Settings > Data Connections > UniFi first."
)


class UnifiError(RuntimeError):
    """A UniFi request failed (non-2xx, transport error, bad payload)."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 code: str = "unifi_request_failed"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class UnifiAuthError(UnifiError):
    """The controller rejected the API key (401/403)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message, status_code=status_code, code="unifi_auth_failed")


# ---------------------------------------------------------------------------
# Admin (server-level) configuration
# ---------------------------------------------------------------------------

def normalize_host(raw: Any) -> str:
    """Normalize the admin-typed controller host to an ``https://`` origin.

    Accepts a bare hostname / IP (``192.168.1.1``, ``unifi.local:8443``) or
    an ``https://`` URL. Only the origin survives: a path, query, or
    fragment is rejected because every request path is appended by the
    plugin. ``http://`` is rejected -- UniFi OS only serves the Integration
    APIs over TLS.
    """
    if not isinstance(raw, str):
        raise ValueError("Controller host must be a string.")
    text = raw.strip()
    if not text:
        raise ValueError("Controller host is required.")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme != "https":
        raise ValueError(
            "Controller host must be an https:// URL (UniFi OS only serves "
            "its API over TLS), e.g. https://192.168.1.1."
        )
    if not parts.hostname:
        raise ValueError("Controller host must include a hostname or IP address.")
    if parts.username or parts.password:
        raise ValueError("Controller host must not embed credentials.")
    if (parts.path and parts.path != "/") or parts.query or parts.fragment:
        raise ValueError(
            "Controller host must be just the host (and optional port), "
            "with no path -- e.g. https://192.168.1.1 or https://unifi.local:443."
        )
    hostname = parts.hostname
    if not _HOSTNAME_RE.match(hostname) and not hostname.startswith("["):
        # IPv6 literals come back without brackets from urlsplit; rebuild.
        if ":" in hostname:
            hostname = f"[{hostname}]"
        else:
            raise ValueError(f"Controller host {hostname!r} is not a valid hostname or IP.")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("Controller host has an invalid port.")
    origin = f"https://{hostname}"
    if port and port != 443:
        origin += f":{port}"
    return origin


def validate_unifi_credentials(values: dict) -> dict:
    """Admin-save normalization hook: strip whitespace, normalize the host.

    Raises ValueError (surfaced as a 400 by the generic credentials
    endpoint) for a malformed host.
    """
    out = {
        key: (value.strip() if isinstance(value, str) else value)
        for key, value in values.items()
    }
    host = out.get("host")
    if host:
        out["host"] = normalize_host(host)
    return out


def unifi_is_configured(config: dict) -> bool:
    """Admin ``is_configured`` predicate: a controller host is set."""
    return bool(config.get("host"))


def load_unifi_config() -> dict:
    """Load the admin UniFi config from the per-service credential store.

    Store reads are fresh on every call so admin edits (host change, TLS
    switch) take effect immediately. Raises a 500 HTTPException when
    unconfigured (mirrors the other plugins' loaders).
    """
    from fastapi import HTTPException

    from config.service_credentials import read_service_credentials

    stored = read_service_credentials(SERVICE_ID)
    if stored and unifi_is_configured(stored):
        return stored
    raise HTTPException(
        status_code=500,
        detail=(
            "UniFi controller host not configured. Set it in Settings > "
            "Service Credentials > UniFi (admin)."
        ),
    )


def verify_tls_enabled(config: dict | None) -> bool:
    """Whether TLS certificate verification is on (only a JSON ``true``)."""
    return (config or {}).get("verify_tls") is True


# ---------------------------------------------------------------------------
# Per-user connection (API keys)
# ---------------------------------------------------------------------------

NETWORK_KEY = "network_api_key"
PROTECT_KEY = "protect_api_key"


def get_user_unifi_blob(user: dict) -> Optional[dict]:
    """The user's stored UniFi blob, or None when no row exists.

    Reads the ``user_service_credentials`` row attached to the user dict
    by db/user_store.py (service key ``unifi``).
    """
    rows = user.get("service_credentials") or {}
    blob = (rows.get(SERVICE_ID) or {}).get("oauth_blob")
    return blob if isinstance(blob, dict) else None


def _key_from_blob(blob: dict | None, key: str) -> Optional[str]:
    value = (blob or {}).get(key)
    return value if isinstance(value, str) and value.strip() else None


def get_network_api_key(user: dict) -> Optional[str]:
    return _key_from_blob(get_user_unifi_blob(user), NETWORK_KEY)


def get_protect_api_key(user: dict) -> Optional[str]:
    return _key_from_blob(get_user_unifi_blob(user), PROTECT_KEY)


def unifi_connected(row: dict) -> bool:
    """``UserConnectionSpec.connected`` hook: at least one API key stored."""
    blob = row.get("oauth_blob")
    if not isinstance(blob, dict):
        return False
    return bool(_key_from_blob(blob, NETWORK_KEY) or _key_from_blob(blob, PROTECT_KEY))


def mask_key(key: str | None) -> str:
    """Render an API key for display: last 4 characters only."""
    if not key:
        return ""
    tail = key[-4:]
    return "•" * max(len(key) - len(tail), 0) + tail


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _extract_error_message(resp: httpx.Response) -> str:
    """Best-effort human message from an Integration API error body.

    The Integration APIs answer ``{"statusCode", "statusName", "message"}``;
    the legacy Network API answers ``{"meta": {"rc": "error", "msg"}}``.
    """
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        if isinstance(payload.get("message"), str) and payload["message"]:
            name = payload.get("statusName")
            return f"{payload['message']}" + (f" ({name})" if name else "")
        meta = payload.get("meta")
        if isinstance(meta, dict) and meta.get("msg"):
            return str(meta["msg"])
    text = (resp.text or "").strip()
    return text[:300] if text else f"HTTP {resp.status_code}"


class UnifiClient:
    """A thin async HTTP client for one controller + one API key.

    ``transport`` is a test seam (``httpx.MockTransport``). One client per
    tool call; the underlying ``httpx.AsyncClient`` is created lazily and
    closed by ``aclose()`` / the async context manager.
    """

    def __init__(self, host: str, api_key: str, *, verify_tls: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.host = host.rstrip("/")
        self._api_key = api_key
        self._verify_tls = verify_tls
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "UnifiClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "base_url": self.host,
                "timeout": _HTTP_TIMEOUT,
                "headers": {
                    "X-API-KEY": self._api_key,
                    "Accept": "application/json",
                    "User-Agent": "Quest/1.0",
                },
                "follow_redirects": False,
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            else:
                kwargs["verify"] = self._verify_tls
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(self, method: str, path: str, *, params: dict | None = None,
                      timeout: float | None = None, accept: str | None = None,
                      ) -> httpx.Response:
        """Issue one request; raise :class:`UnifiError` on transport
        failure or a non-2xx status (``UnifiAuthError`` for 401/403)."""
        client = self._ensure_client()
        headers = {"Accept": accept} if accept else None
        try:
            resp = await client.request(
                method, path, params=params, headers=headers,
                timeout=timeout if timeout is not None else _HTTP_TIMEOUT,
            )
        except httpx.ConnectError as exc:
            raise UnifiError(
                f"Could not connect to the UniFi controller at {self.host}: {exc}",
                code="unifi_unreachable",
            ) from exc
        except httpx.TimeoutException as exc:
            raise UnifiError(
                f"The UniFi controller at {self.host} timed out: {exc}",
                code="unifi_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise UnifiError(f"UniFi request failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise UnifiAuthError(
                "The UniFi controller rejected the API key "
                f"({_extract_error_message(resp)}). Re-enter the key in "
                "Settings > Data Connections > UniFi.",
                status_code=resp.status_code,
            )
        if resp.status_code == 404:
            raise UnifiError(
                f"Not found: {_extract_error_message(resp)}",
                status_code=404, code="unifi_not_found",
            )
        if resp.status_code >= 400:
            raise UnifiError(
                f"UniFi returned HTTP {resp.status_code}: {_extract_error_message(resp)}",
                status_code=resp.status_code,
            )
        return resp

    async def get_json(self, path: str, *, params: dict | None = None) -> Any:
        resp = await self.request("GET", path, params=params)
        try:
            return resp.json()
        except ValueError as exc:
            raise UnifiError(
                f"UniFi returned a non-JSON response for {path}."
            ) from exc

    async def get_bytes(self, path: str, *, params: dict | None = None,
                        timeout: float | None = None, accept: str = "*/*",
                        ) -> tuple[bytes, str]:
        """GET a binary body; returns ``(bytes, content_type)``."""
        resp = await self.request(
            "GET", path, params=params, timeout=timeout, accept=accept,
        )
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    async def get_paged(self, path: str, *, params: dict | None = None,
                        max_items: int = MAX_COLLECTED_ITEMS) -> list[Any]:
        """Collect every page of an Integration API list endpoint.

        The list endpoints answer ``{offset, limit, count, totalCount,
        data: [...]}``; a bare list is tolerated (some Protect endpoints
        return one).
        """
        items: list[Any] = []
        offset = 0
        while True:
            page_params = dict(params or {})
            page_params.update({"offset": offset, "limit": PAGE_LIMIT})
            payload = await self.get_json(path, params=page_params)
            if isinstance(payload, list):
                items.extend(payload)
                break
            if not isinstance(payload, dict):
                raise UnifiError(f"Unexpected response shape from {path}.")
            data = payload.get("data")
            if not isinstance(data, list):
                raise UnifiError(f"Unexpected response shape from {path} (no data list).")
            items.extend(data)
            total = payload.get("totalCount")
            count = payload.get("count", len(data))
            offset += max(int(count or 0), 0)
            if not data or offset >= int(total or 0) or len(items) >= max_items:
                break
        return items[:max_items]


def make_client(config: dict, api_key: str, *,
                transport: httpx.AsyncBaseTransport | None = None) -> UnifiClient:
    """A client for the admin-configured controller and the given key."""
    return UnifiClient(
        config["host"], api_key,
        verify_tls=verify_tls_enabled(config), transport=transport,
    )


# ---------------------------------------------------------------------------
# Key verification (used by the connect popup)
# ---------------------------------------------------------------------------

async def verify_network_key(config: dict, api_key: str, *, transport=None) -> dict:
    """Probe the Network Integration API with a key; returns its info payload."""
    async with make_client(config, api_key, transport=transport) as client:
        info = await client.get_json(f"{NETWORK_API_PREFIX}/info")
    return info if isinstance(info, dict) else {}


async def verify_protect_key(config: dict, api_key: str, *, transport=None) -> dict:
    """Probe the Protect Integration API with a key; returns its info payload."""
    async with make_client(config, api_key, transport=transport) as client:
        info = await client.get_json(f"{PROTECT_API_PREFIX}/meta/info")
    return info if isinstance(info, dict) else {}
