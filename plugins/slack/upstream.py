"""Slack upstream client and read/send endpoint functions (plugin module).

Moved from the pre-plugin ``api/slack.py``. These are plain async
functions called by the plugin's tool handlers (plugins/slack/tools.py)
and action-request handlers (plugins/slack/handlers.py) -- there are no
HTTP routes. Errors travel as ``fastapi.HTTPException`` and are converted
to structured ``{"error": ...}`` JSON by the tool layer.

Per-user Slack OAuth data lives in the generic ``user_service_credentials``
table (service="slack", token JSON in ``oauth_blob``) -- written by the
plugin's OAuth callback (plugins/slack/oauth.py) and attached to user
dicts as ``user["service_credentials"]["slack"]``. The shared bot token
and Socket Mode app token are admin credentials in the ``slack`` service
credential store entry; their loaders stay in core ``auth/config.py``
because the (core) Slack Socket Mode worker needs them too.
"""

import asyncio
import logging
from typing import Optional

import httpx
from fastapi import HTTPException

from auth.config import load_slack_bot_token
from chat.tool_notices import ToolResultWithNotices

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"
MAX_LIMIT = 50

# User token scopes requested by the OAuth flow (reading messages,
# searching, sending as the user).
SLACK_USER_SCOPES = (
    "search:read",
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "users:read",
    "im:write",
    "chat:write",
)


# ---------------------------------------------------------------------------
# Credential access (per-user oauth_blob row + admin store predicates)
# ---------------------------------------------------------------------------

def get_user_slack_oauth(user: dict) -> dict:
    """The user's Slack OAuth blob from their service-credential row.

    Returns an empty dict when the user has not connected Slack.
    """
    row = (user.get("service_credentials") or {}).get("slack") or {}
    return row.get("oauth_blob") or {}


def slack_connected(row: dict) -> bool:
    """UserConnectionSpec.connected predicate over the stored row."""
    return bool((row.get("oauth_blob") or {}).get("access_token"))


def slack_is_configured(config: dict) -> bool:
    """Admin-card predicate: the OAuth app client credentials are set.

    The bot / Socket Mode tokens are optional extras (self-DM sends and
    the Socket Mode worker degrade gracefully without them).
    """
    return bool(config.get("client_id")) and bool(config.get("client_secret"))


def get_slack_token(user: dict) -> Optional[str]:
    """Get the user's Slack OAuth token from their credential row."""
    return get_user_slack_oauth(user).get("access_token") or None


def get_slack_user_id(user: dict) -> Optional[str]:
    """Get the user's Slack user ID from their credential row."""
    return get_user_slack_oauth(user).get("user_id") or None


def get_slack_bot_token() -> str:
    """Get the shared Slack bot token from the admin credential store.

    The bot token is a shared credential installed once by an admin,
    used for write operations and org-wide queries.
    """
    return load_slack_bot_token()


# ---------------------------------------------------------------------------
# Shared httpx client with connection pooling
# ---------------------------------------------------------------------------

_slack_http_client: httpx.AsyncClient | None = None


def _get_slack_client() -> httpx.AsyncClient:
    """Return the module-level shared httpx.AsyncClient, creating it on first use.

    The client uses connection pooling (max 20 connections, 10 keep-alive)
    and a 30-second timeout to match other API modules.
    """
    global _slack_http_client
    if _slack_http_client is None:
        _slack_http_client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _slack_http_client


async def _slack_request_with_retry(method: str, url: str, headers: dict, **kwargs) -> httpx.Response:
    """Make an HTTP request to Slack with retry logic.

    Retries up to 3 attempts on:
    - httpx.TimeoutException: waits 1 second between retries
    - HTTP 429 (rate limited): respects Retry-After header (capped at 60s)

    Non-retryable errors and successful responses are returned immediately.
    On exhaustion: re-raises the last timeout exception or returns the last 429 response.
    """
    client = _get_slack_client()
    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers, **kwargs)
            else:
                response = await client.post(url, headers=headers, **kwargs)

            # Retry on 429 rate limit
            if response.status_code == 429:
                if attempt < max_attempts - 1:
                    try:
                        retry_after = int(response.headers.get("Retry-After", "1"))
                    except (ValueError, TypeError):
                        retry_after = 1
                    retry_after = min(retry_after, 60)
                    logger.warning(
                        "Slack API rate limited (429), retrying after %ds (attempt %d/%d)",
                        retry_after, attempt + 1, max_attempts,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                # Last attempt exhausted -- return the 429 response
                return response

            return response

        except httpx.TimeoutException:
            if attempt < max_attempts - 1:
                logger.warning(
                    "Slack API request timed out, retrying in 1s (attempt %d/%d)",
                    attempt + 1, max_attempts,
                )
                await asyncio.sleep(1)
            else:
                raise


def _with_limit_cap_notice(requested_limit: int, data: dict) -> dict | ToolResultWithNotices:
    """Wrap the response with a limit-cap notice if the limit was capped.

    If the requested limit exceeds MAX_LIMIT, returns a ToolResultWithNotices
    so the tool dispatch layer can append a text warning as an extra_part.
    Otherwise returns the data dict unchanged.
    """
    if requested_limit > MAX_LIMIT:
        return ToolResultWithNotices(
            result=data,
            notices=[
                f"Your requested limit of {requested_limit} was capped to "
                f"{MAX_LIMIT}. Use the next_cursor parameter to paginate "
                f"for more results."
            ],
        )
    return data


async def resolve_team_id(user: dict, explicit_team_id: Optional[str] = None) -> Optional[str]:
    """Resolve team_id for Slack API calls.

    Org-level tokens require team_id for certain API methods
    (e.g., conversations.list, search.messages, users.list).

    Returns the explicit team_id if provided, otherwise falls back to
    the user's default_team_id from their OAuth data. If the resolved ID
    is an Enterprise ID (E-prefix), calls auth.teams.list via the shared
    bot token to resolve it to a workspace team ID (T-prefix).
    """
    team_id = explicit_team_id
    if not team_id:
        team_id = get_user_slack_oauth(user).get("default_team_id")

    if not team_id:
        return None

    # T-prefix IDs (and other non-E prefixes) are already workspace IDs
    if not team_id.startswith("E"):
        return team_id

    # E-prefix: Enterprise ID -- resolve to a workspace T-prefix ID
    # via auth.teams.list using the shared bot token.
    original_team_id = team_id
    try:
        data = await proxy_slack_bot_get_request("auth.teams.list", {"limit": 100})
        teams = data.get("teams", [])
        if teams:
            resolved = teams[0]["id"]
            logger.info(
                "Resolved Enterprise ID %s to workspace ID %s",
                original_team_id,
                resolved,
            )
            return resolved
        else:
            logger.warning(
                "auth.teams.list returned no teams for Enterprise ID %s; "
                "falling back to original ID",
                original_team_id,
            )
            return original_team_id
    except Exception:
        logger.warning(
            "Failed to resolve Enterprise ID %s via auth.teams.list; "
            "falling back to original ID",
            original_team_id,
            exc_info=True,
        )
        return original_team_id


async def proxy_slack_request(user: dict, method: str, params: dict, team_id: Optional[str] = None) -> dict:
    """Proxy a GET request to the Slack API.

    Args:
        user: User dict with oauth data
        method: Slack API method (e.g., 'conversations.history')
        params: Query parameters to send to Slack
        team_id: Optional workspace ID to inject into params (for org-level installs)

    Returns:
        Slack API response as dict
    """
    token = get_slack_token(user)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authentication_required",
                "message": "Slack authentication required. Please authenticate via /auth/slack"
            }
        )

    # Inject team_id for org-level installs when provided
    if team_id:
        params["team_id"] = team_id

    url = f"{SLACK_API_BASE}/{method}"

    response = await _slack_request_with_retry(
        "GET", url, headers={"Authorization": f"Bearer {token}"}, params=params,
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": "slack_api_error",
                "message": f"Slack API returned {response.status_code}",
                "details": response.text
            }
        )

    data = response.json()

    # Slack returns ok:false on errors
    if not data.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "slack_api_error",
                "message": data.get("error", "Unknown Slack API error"),
                "details": data
            }
        )

    return data


async def proxy_slack_bot_get_request(method: str, params: dict) -> dict:
    """Proxy a GET request to the Slack API using the shared bot token.

    Used for org-wide operations like auth.teams.list that are not
    user-specific.

    Args:
        method: Slack API method (e.g., 'auth.teams.list')
        params: Query parameters to send to Slack

    Returns:
        Slack API response as dict
    """
    token = get_slack_bot_token()
    url = f"{SLACK_API_BASE}/{method}"

    response = await _slack_request_with_retry(
        "GET", url, headers={"Authorization": f"Bearer {token}"}, params=params,
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": "slack_api_error",
                "message": f"Slack API returned {response.status_code}",
                "details": response.text
            }
        )

    data = response.json()

    if not data.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "slack_api_error",
                "message": data.get("error", "Unknown Slack API error"),
                "details": data
            }
        )

    return data


async def proxy_slack_post_request(token: str, method: str, payload: dict) -> dict:
    """Proxy a POST request to the Slack API.

    Args:
        token: Slack OAuth token to use
        method: Slack API method (e.g., 'chat.postMessage')
        payload: JSON payload to send to Slack

    Returns:
        Slack API response as dict
    """
    url = f"{SLACK_API_BASE}/{method}"

    response = await _slack_request_with_retry(
        "POST", url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": "slack_api_error",
                "message": f"Slack API returned {response.status_code}",
                "details": response.text
            }
        )

    data = response.json()

    # Slack returns ok:false on errors
    if not data.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "slack_api_error",
                "message": data.get("error", "Unknown Slack API error"),
                "details": data
            }
        )

    return data


# Slack endpoint functions (called by the tool layer, not HTTP routes)

async def list_conversations(
    user: dict,
    types: Optional[str] = "public_channel,private_channel",
    limit: Optional[int] = 50,
    cursor: Optional[str] = None,
    exclude_archived: Optional[bool] = True,
    team_id: Optional[str] = None,
):
    """List conversations (channels, DMs, group messages).

    Args:
        types: Comma-separated list of types (public_channel, private_channel, mpim, im)
        limit: Maximum number of results (1-50, default 50)
        cursor: Pagination cursor
        exclude_archived: Exclude archived channels
        team_id: Workspace ID (optional, defaults to user's home workspace)
    """
    params = {
        "types": types,
        "limit": min(limit, MAX_LIMIT),
        "exclude_archived": exclude_archived
    }
    if cursor:
        params["cursor"] = cursor

    resolved_team_id = await resolve_team_id(user, team_id)
    data = await proxy_slack_request(user, "conversations.list", params, team_id=resolved_team_id)
    return _with_limit_cap_notice(limit, data)


async def get_conversation_history(
    channel: str,
    user: dict,
    limit: Optional[int] = 50,
    cursor: Optional[str] = None,
    oldest: Optional[str] = None,
    latest: Optional[str] = None,
    inclusive: Optional[bool] = False
):
    """Get messages from a conversation.

    Args:
        channel: Channel ID
        limit: Maximum number of messages (1-50, default 50)
        cursor: Pagination cursor
        oldest: Only messages after this Unix timestamp
        latest: Only messages before this Unix timestamp
        inclusive: Include messages with timestamps matching oldest/latest
    """
    params = {
        "channel": channel,
        "limit": min(limit, MAX_LIMIT),
        "inclusive": inclusive
    }
    if cursor:
        params["cursor"] = cursor
    if oldest:
        params["oldest"] = oldest
    if latest:
        params["latest"] = latest

    data = await proxy_slack_request(user, "conversations.history", params)
    return _with_limit_cap_notice(limit, data)


async def get_conversation_replies(
    channel: str,
    ts: str,
    user: dict,
    limit: Optional[int] = 50,
    cursor: Optional[str] = None,
    oldest: Optional[str] = None,
    latest: Optional[str] = None,
    inclusive: Optional[bool] = False
):
    """Get replies to a thread.

    Args:
        channel: Channel ID
        ts: Thread timestamp (parent message timestamp)
        limit: Maximum number of messages (1-50, default 50)
        cursor: Pagination cursor
        oldest: Only messages after this Unix timestamp
        latest: Only messages before this Unix timestamp
        inclusive: Include messages with timestamps matching oldest/latest
    """
    params = {
        "channel": channel,
        "ts": ts,
        "limit": min(limit, MAX_LIMIT),
        "inclusive": inclusive
    }
    if cursor:
        params["cursor"] = cursor
    if oldest:
        params["oldest"] = oldest
    if latest:
        params["latest"] = latest

    data = await proxy_slack_request(user, "conversations.replies", params)
    return _with_limit_cap_notice(limit, data)


async def search_messages(
    query: str,
    user: dict,
    count: Optional[int] = 20,
    page: Optional[int] = 1,
    sort: Optional[str] = "score",
    sort_dir: Optional[str] = "desc",
    team_id: Optional[str] = None,
):
    """Search for messages.

    Args:
        query: Search query (supports Slack search syntax)
        count: Number of results per page (max 100, default 20)
        page: Page number (1-indexed)
        sort: Sort by 'score' or 'timestamp'
        sort_dir: Sort direction 'asc' or 'desc'
        team_id: Workspace ID (optional, defaults to user's home workspace)
    """
    params = {
        "query": query,
        "count": min(count, 100),
        "page": page,
        "sort": sort,
        "sort_dir": sort_dir
    }

    resolved_team_id = await resolve_team_id(user, team_id)
    return await proxy_slack_request(user, "search.messages", params, team_id=resolved_team_id)


async def get_user_info(
    user_id: str,
    user: dict,
):
    """Get information about a user.

    Args:
        user_id: Slack user ID
    """
    params = {"user": user_id}
    return await proxy_slack_request(user, "users.info", params)


async def list_users(
    user: dict,
    limit: Optional[int] = 50,
    cursor: Optional[str] = None,
    team_id: Optional[str] = None,
):
    """List users in the workspace.

    Args:
        limit: Maximum number of results (1-50, default 50)
        cursor: Pagination cursor
        team_id: Workspace ID (optional, defaults to user's home workspace)
    """
    params = {"limit": min(limit, MAX_LIMIT)}
    if cursor:
        params["cursor"] = cursor

    resolved_team_id = await resolve_team_id(user, team_id)
    data = await proxy_slack_request(user, "users.list", params, team_id=resolved_team_id)
    return _with_limit_cap_notice(limit, data)


async def list_teams(
    user: dict,  # noqa: ARG001 -- kept so callers must supply an authenticated user
    limit: Optional[int] = 100,
    cursor: Optional[str] = None,
):
    """List workspaces accessible via the shared bot token.

    Uses auth.teams.list with the org-level bot token to return all
    workspaces in the organization. The ``user`` parameter is retained
    so that only authenticated callers reach this function.

    Args:
        limit: Maximum number of results (default 100)
        cursor: Pagination cursor
    """
    params = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    return await proxy_slack_bot_get_request("auth.teams.list", params)


async def send_dm_to_self(
    user: dict,
    text: str,
    files: list[tuple[str, bytes]] | None = None,
) -> dict:
    """Send a DM to yourself on Slack (from the Quest bot), optionally with files.

    Uses the shared bot token for sending and the user's Slack user ID
    (from their OAuth data) as the recipient. Text-only messages go
    straight to ``chat.postMessage`` addressed to the bare user ID
    (needs just ``chat:write``, the historical scope footprint). With
    *files*, Slack's external upload flow is used instead so the text and
    all files land as ONE Slack message: ``conversations.open`` resolves
    the bot<->user DM channel (this call is why file sends additionally
    need the ``im:write`` bot scope, alongside ``files:write``), then
    ``files.getUploadURLExternal`` per file, a raw POST of the bytes to
    the returned URL, and a single ``files.completeUploadExternal`` into
    that channel with *text* as the initial comment.

    Args:
        user: User dict with oauth data (recipient resolved from their
            Slack user ID).
        text: Message text to send.
        files: Optional ``(filename, data)`` pairs to upload.
    """
    bot_token = get_slack_bot_token()

    slack_user_id = get_slack_user_id(user)
    if not slack_user_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_user_id",
                "message": "No Slack user ID found. Please authenticate via /auth/slack first."
            }
        )

    if not files:
        # Unfurling is disabled on purpose: this tool sends model-written
        # text with NO approval card, so a prompt-injected URL in the
        # message would otherwise make Slack's link crawler fetch it --
        # an outbound request to an arbitrary host carrying whatever the
        # model put in the query string (its prompt-visible API key,
        # memory or email content, ...). The DM still reaches only the
        # user; only Slack's server-side fetch of the URL is suppressed.
        return await proxy_slack_post_request(
            bot_token,
            "chat.postMessage",
            {
                "channel": slack_user_id,
                "text": text,
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )

    # Resolve the bot<->user DM channel (files.completeUploadExternal
    # rejects bare user ids).
    open_data = await proxy_slack_post_request(
        bot_token, "conversations.open", {"users": slack_user_id},
    )
    channel_id = open_data["channel"]["id"]

    uploaded: list[dict] = []
    for filename, data in files:
        url_data = await proxy_slack_bot_get_request(
            "files.getUploadURLExternal",
            {"filename": filename, "length": len(data)},
        )
        upload_resp = await _slack_request_with_retry(
            "POST", url_data["upload_url"], headers={}, content=data,
        )
        if upload_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "slack_upload_failed",
                    "message": (
                        f"Uploading {filename!r} to Slack failed with "
                        f"HTTP {upload_resp.status_code}"
                    ),
                },
            )
        uploaded.append({"id": url_data["file_id"], "title": filename})

    return await proxy_slack_post_request(
        bot_token,
        "files.completeUploadExternal",
        {
            "files": uploaded,
            "channel_id": channel_id,
            "initial_comment": text,
        },
    )
