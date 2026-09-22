"""Slack action-request handlers and resolver helpers (plugin module).

``send_slack_message`` (preferred: any channel or DM, optional threaded
reply) and the legacy ``send_slack_dm``. Both type names predate the
plugin packaging and are persisted in old ``action_requests`` rows, so
they ride on the manifest's ``unprefixed_action_types`` grandfather list
(the Twitter/X ``send_twitter_dm`` precedent).

Both handlers send with the user's own per-user Slack OAuth token (the
message appears as coming from the user themselves), read from the
generic ``user_service_credentials`` row via
``plugins.slack.upstream.get_user_slack_oauth``. The preview-name /
thread-context enrichment that used to live in the core ladder in
chat/gemini_api/turn_tools.py is the ``enrich_params_for_preview`` hook
here.
"""

import logging
import re

import httpx

from chat.action_request_types.base import ActionRequestHandler
from chat.action_request_types.helpers import _extract_first_name
from chat.action_request_types._param_validation import reject_unknown_params
from config.server_config import get_app_base_url

from plugins.slack.upstream import SLACK_API_BASE, get_user_slack_oauth

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message-block and preview-resolver helpers
# ---------------------------------------------------------------------------

def _quest_attribution_text(first_name: str = "") -> str:
    """Return Slack mrkdwn-formatted Quest attribution text.

    If a first name is provided, returns:
        _<first_name> created and sent this message using Quest_
    Otherwise, falls back to:
        _Created and sent using Quest_

    "Quest" is hyperlinked to the deployment's public URL (the
    ``app_base_url`` server config key) when one is configured, and plain
    text otherwise.
    """
    app_base_url = get_app_base_url()
    quest_link = f"<{app_base_url}/|Quest>" if app_base_url else "Quest"
    if first_name.strip():
        return f"_{first_name} created and sent this message using {quest_link}_"
    return f"_Created and sent using {quest_link}_"


def _build_slack_blocks(message: str, first_name: str = "") -> list[dict]:
    """Build Block Kit blocks for a Slack message with Quest attribution.

    Returns a list of Block Kit blocks: a section block with the message
    text followed by a context block with a Quest attribution suffix that
    includes the sender's first name when available.
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": message,
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": _quest_attribution_text(first_name),
                }
            ],
        },
    ]


async def _resolve_slack_channel_name(channel_id: str, user_token: str) -> str | None:
    """Resolve a Slack channel ID to a human-readable name.

    Returns a formatted name like '#general', '@alice', or the raw ID on failure.
    Uses conversations.info for channels and users.info for user IDs.
    """
    try:
        async with httpx.AsyncClient() as client:
            if channel_id.startswith("U") or channel_id.startswith("W"):
                # It's a user ID -- look up their display name
                resp = await client.get(
                    f"{SLACK_API_BASE}/users.info",
                    params={"user": channel_id},
                    headers={"Authorization": f"Bearer {user_token}"},
                )
                data = resp.json()
                if data.get("ok"):
                    user_obj = data["user"]
                    # Prefer display_name, fall back to real_name, then name
                    name = (
                        user_obj.get("profile", {}).get("display_name")
                        or user_obj.get("real_name")
                        or user_obj.get("name")
                        or channel_id
                    )
                    return f"@{name}"
            else:
                # It's a channel/group/DM ID -- use conversations.info
                resp = await client.get(
                    f"{SLACK_API_BASE}/conversations.info",
                    params={"channel": channel_id},
                    headers={"Authorization": f"Bearer {user_token}"},
                )
                data = resp.json()
                if data.get("ok"):
                    channel = data["channel"]
                    if channel.get("is_im"):
                        # DM channel -- resolve the other user's name
                        other_user_id = channel.get("user")
                        if other_user_id:
                            return await _resolve_slack_channel_name(other_user_id, user_token)
                        return channel_id
                    name = channel.get("name", channel_id)
                    return f"#{name}"
    except Exception:
        logger.warning("[resolve_slack_channel_name] Failed to resolve %s", channel_id, exc_info=True)
    return None


async def _resolve_slack_user_name(user_id: str, user_token: str) -> str:
    """Resolve a Slack user ID to a display name.

    Returns a name like 'Alice Smith' or falls back to the raw user ID.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SLACK_API_BASE}/users.info",
                params={"user": user_id},
                headers={"Authorization": f"Bearer {user_token}"},
            )
            data = resp.json()
            if data.get("ok"):
                user_obj = data["user"]
                return (
                    user_obj.get("profile", {}).get("display_name")
                    or user_obj.get("real_name")
                    or user_obj.get("name")
                    or user_id
                )
    except Exception:
        logger.warning("[resolve_slack_user_name] Failed to resolve %s", user_id, exc_info=True)
    return user_id


async def _fetch_slack_thread_context(
    channel_id: str, thread_ts: str, user_token: str
) -> dict | None:
    """Fetch thread context from Slack for preview display.

    Uses conversations.replies to get the parent message and up to 3 latest
    replies. Returns a dict with 'parent' and 'replies' keys, or None on
    failure.

    Each message dict contains 'user' (display name) and 'text' (truncated
    to 500 chars).
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SLACK_API_BASE}/conversations.replies",
                params={
                    "channel": channel_id,
                    "ts": thread_ts,
                    "limit": 50,
                },
                headers={"Authorization": f"Bearer {user_token}"},
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning(
                    "[fetch_slack_thread_context] conversations.replies failed: %s",
                    data.get("error"),
                )
                return None

            messages = data.get("messages", [])
            if not messages:
                return None

            # First message is always the parent
            parent_msg = messages[0]
            # Remaining messages are replies; take the 3 latest
            replies = messages[1:][-3:] if len(messages) > 1 else []

            # Resolve user names for parent and replies
            user_ids = set()
            user_ids.add(parent_msg.get("user", ""))
            for reply in replies:
                user_ids.add(reply.get("user", ""))
            user_ids.discard("")

            # Batch-resolve all unique user IDs
            name_cache: dict[str, str] = {}
            for uid in user_ids:
                name_cache[uid] = await _resolve_slack_user_name(uid, user_token)

            def _format_msg(msg: dict) -> dict:
                uid = msg.get("user", "")
                text = msg.get("text", "")
                if len(text) > 500:
                    text = text[:500] + "..."
                return {
                    "user": name_cache.get(uid, uid),
                    "text": text,
                }

            reply_count = len(messages) - 1  # total replies in thread

            return {
                "parent": _format_msg(parent_msg),
                "replies": [_format_msg(r) for r in replies],
                "reply_count": reply_count,
            }
    except Exception:
        logger.warning(
            "[fetch_slack_thread_context] Failed for channel=%s thread_ts=%s",
            channel_id, thread_ts, exc_info=True,
        )
    return None


# ---------------------------------------------------------------------------
# send_slack_message
# ---------------------------------------------------------------------------

# `channel_name` and `thread_context` are server-injected after
# validation by the enrich_params_for_preview hook below; they are
# NOT model-supplied params and must NOT appear in this allow-list.
_MESSAGE_ALLOWED_PARAMS = frozenset({"channel_id", "message", "thread_ts"})

# Slack object-id shape: a prefix character identifying the entity type
# followed by an upper-case alphanumeric tail. Slack ids are 9-11 chars
# in practice but Slack does not commit to an upper bound, so we use a
# permissive {8,} tail (9 chars total) with no maximum.
_CHANNEL_ID_PREFIXES = ("C", "G", "D", "U", "W")
_CHANNEL_ID_RE = re.compile(r"^[CGDUW][A-Z0-9]{8,}$")

# Slack message timestamp: `<unix_seconds>.<microseconds>`. Both halves
# are non-empty digit runs separated by exactly one dot. Permalink-style
# ids (`p1727991234000200`) and bare unix timestamps are rejected.
_THREAD_TS_RE = re.compile(r"^[0-9]+\.[0-9]+$")


def _truncate_for_error(value: str, limit: int = 100) -> str:
    """Truncate ``value`` for inclusion in a ValueError message.

    Keeps error strings short so the model is not handed back a giant
    URL or pasted blob it should not be echoing.
    """
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


class SendSlackMessageHandler(ActionRequestHandler):
    """Send a Slack message to any channel or DM, from the authenticated user.

    Accepts a channel_id (C-prefix for channels, G-prefix for groups,
    D-prefix for DMs) or a user ID (U/W-prefix, which triggers DM open).
    Uses the user's per-user Slack OAuth token so the message appears as
    coming from the user themselves.
    """

    @property
    def type_name(self) -> str:
        return "send_slack_message"

    @property
    def display_name(self) -> str:
        return "Send Slack Message"

    @property
    def preview_fields(self) -> list[str]:
        return ["channel_id", "message"]

    @property
    def approve_label(self) -> str:
        return "Send"

    @property
    def resolved_label(self) -> str:
        return "Sent"

    def summary_snippet(self, params: dict) -> str:
        return str(params.get("message") or "")

    async def render_preview(self, params: dict, user: dict | None = None) -> list[dict]:
        fields = []
        to_value = params.get("channel_name") or params.get("channel_id") or "Unknown channel"
        fields.append({"key": "To", "value": to_value})
        fields.append({"key": "Message", "value": params.get("message", "")})

        # Thread context: flatten to a readable text string
        if params.get("thread_ts"):
            thread_context = params.get("thread_context")
            if thread_context and isinstance(thread_context, dict):
                lines = []
                parent = thread_context.get("parent")
                if parent:
                    lines.append(f"{parent.get('user', 'Unknown')}: {parent.get('text', '')}")
                reply_count = thread_context.get("reply_count", 0)
                replies = thread_context.get("replies", [])
                shown = len(replies)
                if reply_count > shown:
                    lines.append(f"--- {reply_count - shown} earlier {'reply' if reply_count - shown == 1 else 'replies'} ---")
                for reply in replies:
                    lines.append(f"{reply.get('user', 'Unknown')}: {reply.get('text', '')}")
                fields.append({"key": "Thread", "value": "\n".join(lines)})
            else:
                fields.append({"key": "Thread", "value": "Reply in thread"})
        return fields

    def validate_params(self, params: dict) -> dict:
        reject_unknown_params(self.type_name, params, _MESSAGE_ALLOWED_PARAMS)

        channel_id = params.get("channel_id")
        message = params.get("message")

        if not channel_id or not str(channel_id).strip():
            raise ValueError("Missing required parameter: channel_id (Slack channel or user ID)")
        if not message or not str(message).strip():
            raise ValueError("Missing required parameter: message")
        if len(str(message)) > 3000:
            raise ValueError("Message exceeds Slack's 3000 character limit for formatted messages")

        channel_id_str = str(channel_id).strip()
        # Format-validate the channel id so malformed values (#channel,
        # @user, permalink URLs, lowercase ids, ints) are rejected up
        # front rather than persisting to action_requests and failing
        # inside execute() with an opaque Slack `channel_not_found` error.
        if not channel_id_str.startswith(_CHANNEL_ID_PREFIXES):
            raise ValueError(
                f"channel_id must be a Slack object id starting with one of "
                f"{list(_CHANNEL_ID_PREFIXES)} (C=channel, G=group, D=DM, "
                f"U=user, W=enterprise user); got {_truncate_for_error(channel_id_str)!r}"
            )
        if not _CHANNEL_ID_RE.match(channel_id_str):
            raise ValueError(
                f"channel_id must match pattern ^[CGDUW][A-Z0-9]{{8,}}$ "
                f"(uppercase alphanumeric Slack id); got "
                f"{_truncate_for_error(channel_id_str)!r}"
            )

        validated = {
            "channel_id": channel_id_str,
            "message": str(message).strip(),
        }

        thread_ts = params.get("thread_ts")
        # An empty/whitespace-only thread_ts is treated as absent and not
        # carried forward, matching the prior handler shape.
        if thread_ts and str(thread_ts).strip():
            thread_ts_str = str(thread_ts).strip()
            if not _THREAD_TS_RE.match(thread_ts_str):
                raise ValueError(
                    f"thread_ts must match pattern ^[0-9]+\\.[0-9]+$ "
                    f"(Slack message timestamp like '1727991234.000200', "
                    f"not a permalink id); got "
                    f"{_truncate_for_error(thread_ts_str)!r}"
                )
            validated["thread_ts"] = thread_ts_str

        return validated

    async def enrich_params_for_preview(self, params: dict, user: dict) -> None:
        """Resolve a human-readable channel name (and thread context for
        threaded replies) into server-injected params for the approval
        card. Never raises -- the card simply renders without them."""
        user_token = get_user_slack_oauth(user).get("access_token")
        if not user_token:
            return

        channel_name = await _resolve_slack_channel_name(
            params["channel_id"], user_token
        )
        if channel_name:
            params["channel_name"] = channel_name

        # Fetch thread context for preview when replying to a thread
        if "thread_ts" in params:
            thread_context = await _fetch_slack_thread_context(
                params["channel_id"],
                params["thread_ts"],
                user_token,
            )
            if thread_context:
                params["thread_context"] = thread_context

    async def execute(
        self,
        params: dict,
        user: dict,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Send a message to any Slack channel or DM."""
        user_token = get_user_slack_oauth(user).get("access_token")

        if not user_token:
            raise RuntimeError(
                "Slack not connected. Please connect Slack via Settings > Data Connections."
            )

        target_id = params["channel_id"]

        async with httpx.AsyncClient() as client:
            # If target is a user ID, open a DM conversation first
            if target_id.startswith("U") or target_id.startswith("W"):
                open_resp = await client.post(
                    f"{SLACK_API_BASE}/conversations.open",
                    json={"users": target_id},
                    headers={
                        "Authorization": f"Bearer {user_token}",
                        "Content-Type": "application/json",
                    },
                )
                open_data = open_resp.json()
                logger.info(
                    "[send_slack_message] conversations.open response: ok=%s, error=%s",
                    open_data.get("ok"), open_data.get("error"),
                )
                if not open_data.get("ok"):
                    error = open_data.get("error", "unknown")
                    needed = open_data.get("needed", "")
                    provided = open_data.get("provided", "")
                    raise RuntimeError(
                        f"Failed to open DM channel: {error} (needed={needed}, provided={provided})"
                    )
                channel_id = open_data["channel"]["id"]
            else:
                channel_id = target_id

            # Send the message via chat.postMessage
            message_text = params["message"]
            first_name = _extract_first_name(user.get("name", ""))
            payload = {
                "channel": channel_id,
                "text": message_text,
                "blocks": _build_slack_blocks(message_text, first_name=first_name),
            }
            if "thread_ts" in params:
                payload["thread_ts"] = params["thread_ts"]
            send_resp = await client.post(
                f"{SLACK_API_BASE}/chat.postMessage",
                json=payload,
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json",
                },
            )
            send_data = send_resp.json()
            logger.info(
                "[send_slack_message] chat.postMessage response: ok=%s, error=%s",
                send_data.get("ok"), send_data.get("error"),
            )
            if not send_data.get("ok"):
                error = send_data.get("error", "unknown")
                needed = send_data.get("needed", "")
                provided = send_data.get("provided", "")
                raise RuntimeError(
                    f"Failed to send message: {error} (needed={needed}, provided={provided})"
                )

        return {
            "success": True,
            "channel_id": channel_id,
            "message_ts": send_data.get("ts", ""),
            "thread_ts": params.get("thread_ts", ""),
        }


# ---------------------------------------------------------------------------
# send_slack_dm (legacy)
# ---------------------------------------------------------------------------

_DM_ALLOWED_PARAMS = frozenset({"user_id", "message"})


class SendSlackDmHandler(ActionRequestHandler):
    """Send a Slack DM to a specified user, from the authenticated user.

    Uses the user's per-user Slack OAuth token (not the bot token) so the
    message appears as coming from the user themselves.
    """

    @property
    def type_name(self) -> str:
        return "send_slack_dm"

    @property
    def display_name(self) -> str:
        return "Send Slack DM"

    @property
    def preview_fields(self) -> list[str]:
        return ["user_id", "message"]

    @property
    def approve_label(self) -> str:
        return "Send"

    @property
    def resolved_label(self) -> str:
        return "Sent"

    def summary_snippet(self, params: dict) -> str:
        return str(params.get("message") or "")

    async def render_preview(self, params: dict, user: dict | None = None) -> list[dict]:
        fields = []
        fields.append({"key": "To", "value": params.get("user_id", "")})
        fields.append({"key": "Message", "value": params.get("message", "")})
        return fields

    def validate_params(self, params: dict) -> dict:
        reject_unknown_params(self.type_name, params, _DM_ALLOWED_PARAMS)

        user_id = params.get("user_id")
        message = params.get("message")

        if not user_id or not str(user_id).strip():
            raise ValueError("Missing required parameter: user_id (Slack user ID)")
        if not message or not str(message).strip():
            raise ValueError("Missing required parameter: message")
        if len(str(message)) > 3000:
            raise ValueError("Message exceeds Slack's 3000 character limit for formatted messages")

        return {
            "user_id": str(user_id).strip(),
            "message": str(message).strip(),
        }

    async def execute(
        self,
        params: dict,
        user: dict,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Send a DM using the user's own Slack OAuth token."""
        user_token = get_user_slack_oauth(user).get("access_token")

        if not user_token:
            raise RuntimeError(
                "Slack not connected. Please connect Slack via Settings > Data Connections."
            )

        async with httpx.AsyncClient() as client:
            # Open/get the DM conversation with the target user
            open_resp = await client.post(
                f"{SLACK_API_BASE}/conversations.open",
                json={"users": params["user_id"]},
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json",
                },
            )
            open_data = open_resp.json()
            logger.info("[send_slack_dm] conversations.open response: ok=%s, error=%s",
                        open_data.get("ok"), open_data.get("error"))
            if not open_data.get("ok"):
                error = open_data.get("error", "unknown")
                needed = open_data.get("needed", "")
                provided = open_data.get("provided", "")
                logger.error("[send_slack_dm] conversations.open failed: error=%s, needed=%s, provided=%s, full_response=%s",
                             error, needed, provided, open_data)
                raise RuntimeError(f"Failed to open DM channel: {error} (needed={needed}, provided={provided})")

            channel_id = open_data["channel"]["id"]

            # Send the DM using chat.postMessage to the DM channel
            message_text = params["message"]
            first_name = _extract_first_name(user.get("name", ""))
            send_resp = await client.post(
                f"{SLACK_API_BASE}/chat.postMessage",
                json={
                    "channel": channel_id,
                    "text": message_text,
                    "blocks": _build_slack_blocks(message_text, first_name=first_name),
                },
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json",
                },
            )
            send_data = send_resp.json()
            logger.info("[send_slack_dm] chat.postMessage response: ok=%s, error=%s",
                        send_data.get("ok"), send_data.get("error"))
            if not send_data.get("ok"):
                error = send_data.get("error", "unknown")
                needed = send_data.get("needed", "")
                provided = send_data.get("provided", "")
                logger.error("[send_slack_dm] chat.postMessage failed: error=%s, needed=%s, provided=%s, full_response=%s",
                             error, needed, provided, send_data)
                raise RuntimeError(f"Failed to send DM: {error} (needed={needed}, provided={provided})")

        return {
            "success": True,
            "channel_id": channel_id,
            "message_ts": send_data.get("ts", ""),
        }
