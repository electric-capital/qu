"""Iru (formerly Kandji) upstream access helpers (plugin module).

Server-side configuration (the admin ``iru`` credential-store entry: the
tenant API URL) and the per-user API token (a ``user_service_credentials``
row with ``secret`` written by the generic ``POST /auth/service-key/iru``
route) both resolve here, so every tool shares one implementation.

The plugin talks to the tenant's Iru Endpoint Management API::

    https://<subdomain>.api.iru.com/api/v1/...        (US)
    https://<subdomain>.api.eu.iru.com/api/v1/...     (EU)

The pre-rebrand ``<subdomain>.api.kandji.io`` / ``.api.eu.kandji.io`` hosts
keep working and are accepted too. Requests carry ``Authorization: Bearer
<token>``. Tokens are tenant-level (Settings > Access > API Token in the
Iru web app) with per-endpoint permission scoping, so a 403 usually means
the token lacks the permission for that endpoint rather than being invalid.
The tenant-wide rate limit is 10,000 requests/hour (and 50/second); a 429
is surfaced as ``iru_rate_limited`` with the ``Retry-After`` value.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

import httpx

logger = logging.getLogger(__name__)

SERVICE_ID = "iru"

API_PREFIX = "/api/v1"

# Hostname suffixes of the Iru / legacy Kandji tenant API domains. The admin
# card only accepts a tenant URL on one of these, so a typo (or the web-app
# host ``<sub>.kandji.io`` instead of the API host) is rejected at save time.
API_HOST_SUFFIXES = (
    ".api.iru.com",
    ".api.eu.iru.com",
    ".api.kandji.io",
    ".api.eu.kandji.io",
)

# The documented hard cap for most offset-paged list endpoints.
MAX_PAGE_LIMIT = 300
# Hard cap on items collected across pages by one helper call.
MAX_COLLECTED_ITEMS = 3000

_HTTP_TIMEOUT = 30.0

_SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

NOT_CONNECTED_ERROR = (
    "Iru is not connected. Add your Iru API token in Settings > Data "
    "Connections > Iru first."
)


class IruError(RuntimeError):
    """An Iru request failed (non-2xx, transport error, bad payload)."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 code: str = "iru_request_failed", retry_after: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after


class IruAuthError(IruError):
    """The tenant rejected the token (401) or it lacks permission (403)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message, status_code=status_code, code="iru_auth_failed")


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value.strip()))


# ---------------------------------------------------------------------------
# Admin (server-level) configuration
# ---------------------------------------------------------------------------

def normalize_api_url(raw: Any) -> str:
    """Normalize the admin-typed tenant API URL to an ``https://`` origin.

    Accepts ``https://acme.api.iru.com``, the bare host ``acme.api.iru.com``
    (the scheme is added), or an ``/api/v1``-suffixed copy of the URL the
    Iru web app shows next to the token (the path is dropped because the
    plugin appends every request path itself). The host must be a tenant
    subdomain on one of :data:`API_HOST_SUFFIXES`; anything else -- the web
    app host ``acme.kandji.io``, ``http://``, embedded credentials, a
    query string -- is rejected.
    """
    if not isinstance(raw, str):
        raise ValueError("Tenant API URL must be a string.")
    text = raw.strip()
    if not text:
        raise ValueError("Tenant API URL is required.")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme != "https":
        raise ValueError("Tenant API URL must use https://, e.g. https://acme.api.iru.com.")
    hostname = (parts.hostname or "").lower()
    if not hostname:
        raise ValueError("Tenant API URL must include a hostname.")
    if parts.username or parts.password:
        raise ValueError("Tenant API URL must not embed credentials.")
    if parts.query or parts.fragment:
        raise ValueError("Tenant API URL must not include a query string or fragment.")
    path = (parts.path or "").rstrip("/")
    if path not in ("", "/api", "/api/v1"):
        raise ValueError(
            "Tenant API URL must be just the host, e.g. https://acme.api.iru.com "
            "(an /api/v1 suffix is tolerated and dropped)."
        )
    if parts.port not in (None, 443):
        raise ValueError("Tenant API URL must not include a custom port.")
    suffix = next((s for s in API_HOST_SUFFIXES if hostname.endswith(s)), None)
    if suffix is None:
        raise ValueError(
            "Tenant API URL must be your tenant's API host: "
            "https://<subdomain>.api.iru.com (US) or https://<subdomain>.api.eu.iru.com "
            "(EU); the legacy <subdomain>.api.kandji.io hosts are accepted too. "
            "Note this is the API host, not the web app host."
        )
    subdomain = hostname[: -len(suffix)]
    if not _SUBDOMAIN_RE.match(subdomain):
        raise ValueError(
            f"{subdomain!r} is not a valid tenant subdomain (letters, digits, hyphens)."
        )
    return f"https://{hostname}"


def validate_iru_credentials(values: dict) -> dict:
    """Admin-save normalization hook: strip whitespace, normalize the URL.

    Raises ValueError (surfaced as a 400 by the generic credentials
    endpoint) for a malformed tenant URL.
    """
    out = {
        key: (value.strip() if isinstance(value, str) else value)
        for key, value in values.items()
    }
    api_url = out.get("api_url")
    if api_url:
        out["api_url"] = normalize_api_url(api_url)
    return out


def iru_is_configured(config: dict) -> bool:
    """Admin ``is_configured`` predicate: a tenant API URL is set."""
    return bool(config.get("api_url"))


def load_iru_config() -> dict:
    """Load the admin Iru config from the per-service credential store.

    Store reads are fresh on every call so admin edits take effect
    immediately. Raises a 500 HTTPException when unconfigured (mirrors the
    other plugins' loaders).
    """
    from fastapi import HTTPException

    from config.service_credentials import read_service_credentials

    stored = read_service_credentials(SERVICE_ID)
    if stored and iru_is_configured(stored):
        return stored
    raise HTTPException(
        status_code=500,
        detail=(
            "Iru tenant API URL not configured. Set it in Settings > "
            "Service Credentials > Iru (admin)."
        ),
    )


# ---------------------------------------------------------------------------
# Per-user connection (API token)
# ---------------------------------------------------------------------------

def validate_api_token(token: str) -> Optional[str]:
    """``UserConnectionSpec.validate_key`` hook: shape check only.

    Iru tokens are opaque strings (UUID-shaped today); the check just
    rejects obviously wrong pastes -- whitespace inside the value, or a
    value too short/long to be a token. Live verification happens on the
    first tool call (a bad token answers ``iru_auth_failed``).
    """
    if any(ch.isspace() for ch in token):
        return "The API token must not contain spaces or line breaks."
    if len(token) < 16:
        return "That is too short to be an Iru API token."
    if len(token) > 256:
        return "That is too long to be an Iru API token."
    return None


def get_user_api_token(user: dict) -> Optional[str]:
    """The user's stored Iru API token, or None when no row / empty.

    Reads the ``user_service_credentials`` row attached to the user dict
    by db/user_store.py (service key ``iru``).
    """
    rows = user.get("service_credentials") or {}
    secret = (rows.get(SERVICE_ID) or {}).get("secret")
    return secret if isinstance(secret, str) and secret.strip() else None


def iru_connected(row: dict) -> bool:
    """``UserConnectionSpec.connected`` hook: a token is stored."""
    secret = row.get("secret")
    return isinstance(secret, str) and bool(secret.strip())


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _extract_error_message(resp: httpx.Response) -> str:
    """Best-effort human message from an Iru error body.

    Iru answers ``{"detail": "..."}`` (DRF style) for most failures; some
    endpoints use ``{"error": ...}`` or ``{"message": ...}``.
    """
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("detail", "message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, (list, dict)) and value:
                return str(value)[:300]
    elif isinstance(payload, list) and payload and isinstance(payload[0], str):
        return payload[0]
    text = (resp.text or "").strip()
    return text[:300] if text else f"HTTP {resp.status_code}"


def _retry_after_seconds(resp: httpx.Response) -> int | None:
    raw = resp.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(int(float(raw)), 0)
    except ValueError:
        return None


class IruClient:
    """A thin async HTTP client for one tenant + one API token.

    ``transport`` is a test seam (``httpx.MockTransport``). One client per
    tool call; the underlying ``httpx.AsyncClient`` is created lazily and
    closed by ``aclose()`` / the async context manager.
    """

    def __init__(self, api_url: str, token: str, *,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_url = api_url.rstrip("/")
        self._token = token
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "IruClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "base_url": self.api_url,
                "timeout": _HTTP_TIMEOUT,
                "headers": {
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                    "User-Agent": "Quest/1.0",
                },
                "follow_redirects": False,
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_json(self, path: str, *, params: dict | None = None) -> Any:
        """GET one endpoint; raise :class:`IruError` on transport failure
        or a non-2xx status (``IruAuthError`` for 401/403)."""
        client = self._ensure_client()
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            resp = await client.get(path, params=clean_params)
        except httpx.ConnectError as exc:
            raise IruError(
                f"Could not connect to the Iru tenant at {self.api_url}: {exc}",
                code="iru_unreachable",
            ) from exc
        except httpx.TimeoutException as exc:
            raise IruError(
                f"The Iru tenant at {self.api_url} timed out: {exc}",
                code="iru_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise IruError(f"Iru request failed: {exc}") from exc

        if resp.status_code == 401:
            raise IruAuthError(
                "Iru rejected the API token "
                f"({_extract_error_message(resp)}). Re-enter the token in "
                "Settings > Data Connections > Iru.",
                status_code=401,
            )
        if resp.status_code == 403:
            raise IruAuthError(
                "Iru refused this request "
                f"({_extract_error_message(resp)}). The API token is valid but "
                "lacks the permission for this endpoint -- grant it under "
                "Settings > Access > API Token in the Iru web app, or use a "
                "different token.",
                status_code=403,
            )
        if resp.status_code == 404:
            raise IruError(
                f"Not found: {_extract_error_message(resp)}",
                status_code=404, code="iru_not_found",
            )
        if resp.status_code == 429:
            retry_after = _retry_after_seconds(resp)
            raise IruError(
                "Iru rate limit reached (10,000 requests/hour per tenant). "
                + (f"Retry after {retry_after}s." if retry_after is not None
                   else "Try again later."),
                status_code=429, code="iru_rate_limited", retry_after=retry_after,
            )
        if resp.status_code >= 400:
            raise IruError(
                f"Iru returned HTTP {resp.status_code}: {_extract_error_message(resp)}",
                status_code=resp.status_code,
            )
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise IruError(f"Iru returned a non-JSON response for {path}.") from exc

    async def get_all_offset_pages(self, path: str, *, params: dict | None = None,
                                   page_limit: int = MAX_PAGE_LIMIT,
                                   max_items: int = MAX_COLLECTED_ITEMS) -> list[Any]:
        """Collect every page of a ``limit``/``offset`` list endpoint.

        Handles the three list shapes Iru uses: a bare JSON list (devices),
        ``{count, next, previous, results}`` (blueprints, statuses, ...),
        and ``{cursor, data}`` (Prism categories). The stop condition
        prefers the payload's own ``next`` / ``count`` metadata so a server
        that caps pages below ``page_limit`` is still walked to the end.
        """
        items: list[Any] = []
        offset = 0
        while True:
            page_params = dict(params or {})
            page_params.update({"limit": page_limit, "offset": offset})
            payload = await self.get_json(path, params=page_params)
            page = page_items(payload, path)
            items.extend(page)
            offset += len(page)
            if not page or len(items) >= max_items:
                break
            if isinstance(payload, dict) and "next" in payload:
                has_more = bool(payload.get("next"))
            elif isinstance(payload, dict) and isinstance(payload.get("count"), int):
                has_more = offset < payload["count"]
            else:
                has_more = len(page) >= page_limit
            if not has_more:
                break
        return items[:max_items]


def page_items(payload: Any, path: str = "") -> list[Any]:
    """The row list of a list-endpoint payload (bare list / results / data)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return rows
    raise IruError(f"Unexpected response shape from {path or 'Iru'} (no row list).")


def cursor_from_next(payload: Any) -> Optional[str]:
    """The ``cursor`` query value of a cursor-paged payload's ``next`` URL."""
    if not isinstance(payload, dict):
        return None
    nxt = payload.get("next")
    if not isinstance(nxt, str) or not nxt:
        return None
    values = parse_qs(urlsplit(nxt).query).get("cursor")
    return values[0] if values else None


def make_client(config: dict, token: str, *,
                transport: httpx.AsyncBaseTransport | None = None) -> IruClient:
    """A client for the admin-configured tenant and the given token."""
    return IruClient(config["api_url"], token, transport=transport)
