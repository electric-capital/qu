"""GitHub upstream access helpers.

Server-side configuration (the admin ``github`` credential-store entry
with the OAuth app's client id/secret) and per-user OAuth tokens (a
``user_service_credentials`` row with token JSON in ``oauth_blob``,
attached to user dicts as ``user["service_credentials"]["github"]``)
both resolve here, so the OAuth router, the authed_get service entry,
and the plugin tool share one implementation.

GitHub OAuth App tokens do not expire, so there is no refresh logic:
the credential loader simply reads the access token out of the stored
blob. A token revoked on GitHub's side surfaces as an upstream 401.
"""

from typing import Optional

# OAuth scopes requested from GitHub. ``repo`` is required for private
# repository read access (GitHub has no read-only repo scope for OAuth
# apps); ``read:org`` covers org membership and org repo listings.
GITHUB_SCOPES = ("repo", "read:org")


def load_github_client_config() -> dict:
    """Load the GitHub OAuth app client config (admin credential store).

    Prefers the per-service credential store and falls back to the
    "github" section of the legacy server_credentials.json. Store reads
    are fresh on every call so admin updates take effect without a
    restart. Raises a 500 HTTPException when unconfigured (mirrors the
    other OAuth client-config loaders in auth/config.py).
    """
    from fastapi import HTTPException

    from config.service_credentials import (
        read_legacy_service_credentials,
        read_service_credentials,
    )

    stored = read_service_credentials("github")
    if stored:
        return stored
    legacy = read_legacy_service_credentials("github")
    if legacy:
        return legacy
    raise HTTPException(
        status_code=500,
        detail=(
            "GitHub OAuth credentials not configured. Set them in "
            "Settings > Service Credentials (admin) or in "
            "server_credentials.json."
        ),
    )


def github_is_configured(config: dict) -> bool:
    """Admin ``is_configured`` predicate: both client fields present."""
    return bool(config.get("client_id") and config.get("client_secret"))


def get_user_github_oauth(user: dict) -> Optional[dict]:
    """The user's stored GitHub OAuth blob, or None when not connected.

    Reads the ``user_service_credentials`` row attached to the user dict
    by db/user_store.py (service key ``github``).
    """
    rows = user.get("service_credentials") or {}
    return (rows.get("github") or {}).get("oauth_blob")


def github_connected(row: dict) -> bool:
    """``UserConnectionSpec.connected`` hook over the stored row."""
    return bool((row.get("oauth_blob") or {}).get("access_token"))


def github_needs_reauth(row: dict) -> bool:
    """``UserConnectionSpec.needs_reauth`` hook: granted-scope check.

    The callback stores the scopes GitHub actually GRANTED (not the ones
    requested), so widening ``GITHUB_SCOPES`` later flags existing
    connections with the "Update Available" re-authorize badge instead of
    failing at call time. Blobs migrated from the pre-plugin
    ``users.github_oauth`` column carry the raw comma-separated ``scope``
    string; both shapes are read here.

    Scopes only exist for classic OAuth Apps (``gho_`` tokens). When the
    client credentials belong to a **GitHub App** instead, the token
    exchange returns a ``ghu_`` user access token with an empty ``scope``
    -- permissions come from the app installation, so the subset check
    would flag re-auth forever. Skip it for those tokens.
    """
    blob = row.get("oauth_blob") or {}
    token = blob.get("access_token") or ""
    if token.startswith("ghu_"):
        return False
    granted = blob.get("scopes")
    if granted is None:
        granted = [s.strip() for s in (blob.get("scope") or "").split(",")]
    granted_set = {s for s in granted if s}
    return not set(GITHUB_SCOPES).issubset(granted_set)


async def load_github_credentials(user: dict):
    """authed_get credential loader: the user's access token, or None."""
    return (get_user_github_oauth(user) or {}).get("access_token")


def inject_github_bearer_auth(token: str, headers: dict) -> None:
    """authed_get auth injector: Bearer token header."""
    headers["Authorization"] = f"Bearer {token}"
