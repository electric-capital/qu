"""SendTwitterDmHandler and Twitter/X resolver helpers (plugin module)."""

import logging
import re

from chat.action_request_types.base import ActionRequestHandler
from chat.action_request_types._param_validation import reject_unknown_params

from plugins.twitter.upstream import (
    TwitterAuthError,
    get_user_twitter_oauth,
    twitter_request,
)

logger = logging.getLogger(__name__)


# `recipient_name` is server-injected after validation by the
# enrich_params_for_preview hook below, resolved from the same
# participant_id / dm_conversation_id that execute() delivers to. It is
# NOT a model-supplied param and must NOT appear in this allow-list: a
# model-chosen label would let the approval card name one account while
# the DM goes to another.
_ALLOWED_PARAMS = frozenset({
    "message",
    "dm_conversation_id",
    "participant_id",
})

# Twitter user ids are 64-bit unsigned integers serialised as numeric
# strings (Twitter publishes them as `id_str` to avoid JSON-number
# truncation). 20 digits = 2**64 - 1 upper bound; the 1-character lower
# bound is permissive so future single-digit ids are not rejected on a
# tightness the API does not require.
_PARTICIPANT_ID_RE = re.compile(r"^[0-9]{1,20}$")

# Twitter DM conversation ids come in two documented shapes (see
# plugins/twitter/instructions.md): a plain numeric group id, or
# `<user_id>-<user_id>` for one-to-one DMs. Twitter does not commit to
# digit-count bounds; we cap each id at 20 digits to match
# `_PARTICIPANT_ID_RE` and reject anything else outright.
_DM_CONVERSATION_ID_RE = re.compile(r"^[0-9]{1,20}(-[0-9]{1,20})?$")


def _truncate_for_error(value: str, limit: int = 100) -> str:
    """Truncate ``value`` for inclusion in a ValueError message.

    Keeps error strings short so the model is not handed back a giant
    URL or pasted blob it should not be echoing.
    """
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


async def _resolve_twitter_recipient_name(
    user: dict,
    dm_conversation_id: str = None,
    participant_id: str = None,
) -> str | None:
    """Resolve a Twitter/X recipient to a human-readable display name.

    If participant_id is given: looks up the user and returns '@username (Name)'.
    If dm_conversation_id is given: fetches conversation participants and returns
    a formatted list, excluding the authenticated user's own account.

    Returns None on failure.
    """
    try:
        if participant_id:
            resp = await twitter_request(
                user,
                "GET",
                f"users/{participant_id}",
                params={"user.fields": "name,username"},
            )
            if not resp.is_success:
                return None
            u = (resp.json().get("data") or {})
            username = u.get("username", "")
            name = u.get("name", "")
            if username:
                return f"@{username} ({name})" if name else f"@{username}"
        elif dm_conversation_id:
            # Get the authenticated user's own ID first
            me_resp = await twitter_request(
                user, "GET", "users/me", params={"user.fields": "id"}
            )
            if not me_resp.is_success:
                return None
            my_id = (me_resp.json().get("data") or {}).get("id")

            resp = await twitter_request(
                user,
                "GET",
                f"dm_conversations/{dm_conversation_id}",
                params={
                    "dm_conversation.fields": "id,dm_conversation_type",
                    "expansions": "participant_ids",
                    "user.fields": "name,username",
                },
            )
            if not resp.is_success:
                return None
            includes = resp.json().get("includes", {})
            participants = includes.get("users", [])
            others = [p for p in participants if p.get("id") != my_id]
            if others:
                parts = [
                    f"@{p['username']} ({p.get('name', '')})"
                    if p.get("name") else f"@{p['username']}"
                    for p in others
                ]
                return ", ".join(parts)
            # Fall back to conversation ID if we can't resolve names
            return dm_conversation_id
    except Exception:
        logger.warning(
            "[resolve_twitter_recipient_name] Failed to resolve recipient",
            exc_info=True,
        )
    return None


class SendTwitterDmHandler(ActionRequestHandler):
    """Send a Twitter/X Direct Message.

    Supports sending to an existing conversation (via dm_conversation_id)
    or creating a new one (via participant_id). Uses the user's per-user
    Twitter OAuth 2.0 access token from the plugin's credential row.
    """

    @property
    def type_name(self) -> str:
        # Grandfathered unprefixed name: persisted in old action_requests
        # rows from before the plugin packaging (see
        # QuestPlugin.unprefixed_action_types).
        return "send_twitter_dm"

    @property
    def display_name(self) -> str:
        return "Send Twitter DM"

    @property
    def preview_fields(self) -> list[str]:
        return ["recipient_name", "message"]

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
        to_value = (
            params.get("recipient_name")
            or params.get("dm_conversation_id")
            or params.get("participant_id")
            or "Unknown recipient"
        )
        fields.append({"key": "To", "value": to_value})
        fields.append({"key": "Message", "value": params.get("message", "")})
        return fields

    def validate_params(self, params: dict) -> dict:
        reject_unknown_params(self.type_name, params, _ALLOWED_PARAMS)

        message = params.get("message")
        dm_conversation_id = params.get("dm_conversation_id")
        participant_id = params.get("participant_id")

        if not message or not str(message).strip():
            raise ValueError("Missing required parameter: message")
        message_str = str(message).strip()
        if len(message_str) > 10000:
            raise ValueError("Message exceeds 10,000 character limit")

        # Treat whitespace-only ids as absent so the both-missing and
        # both-present rules below see a consistent view.
        dm_conversation_id_str = (
            str(dm_conversation_id).strip()
            if dm_conversation_id and str(dm_conversation_id).strip()
            else ""
        )
        participant_id_str = (
            str(participant_id).strip()
            if participant_id and str(participant_id).strip()
            else ""
        )

        if not dm_conversation_id_str and not participant_id_str:
            raise ValueError(
                "Either dm_conversation_id or participant_id is required"
            )
        # On execute, dm_conversation_id wins when both are supplied, so
        # a model that passes both ends up writing to the wrong target.
        # Reject up front and force the model to pick exactly one.
        if dm_conversation_id_str and participant_id_str:
            raise ValueError(
                "Provide exactly one of dm_conversation_id or participant_id, "
                "not both"
            )

        validated: dict = {"message": message_str}

        if dm_conversation_id_str:
            # Format-validate the conversation id so malformed values
            # (Slack-style C-prefix, @handles, URLs, names) are rejected
            # up front rather than persisting to action_requests and
            # failing inside execute() with an opaque Twitter 400.
            if not _DM_CONVERSATION_ID_RE.match(dm_conversation_id_str):
                raise ValueError(
                    f"dm_conversation_id must be a Twitter DM conversation id, "
                    f"either a plain numeric group id or "
                    f"'<user_id>-<user_id>' for a one-to-one DM; got "
                    f"{_truncate_for_error(dm_conversation_id_str)!r}"
                )
            validated["dm_conversation_id"] = dm_conversation_id_str

        if participant_id_str:
            # Format-validate the participant id (Twitter user ids are
            # numeric strings up to 20 digits). Rejects @handles, URLs,
            # the `me` keyword, leading `+`, and `<a>-<b>` shapes that
            # are actually `dm_conversation_id`s.
            if not _PARTICIPANT_ID_RE.match(participant_id_str):
                raise ValueError(
                    f"participant_id must be a numeric Twitter user id "
                    f"(digits only, max 20 digits); got "
                    f"{_truncate_for_error(participant_id_str)!r}"
                )
            validated["participant_id"] = participant_id_str

        return validated

    async def enrich_params_for_preview(self, params: dict, user: dict) -> None:
        """Resolve the recipient to '@username (Name)' for the approval card.

        The label is always derived from the delivery id (participant_id
        or dm_conversation_id) so the card can never name a different
        account than execute() sends to; any pre-existing recipient_name
        is discarded. Never raises -- on resolution failure the card
        falls back to the raw id.
        """
        params.pop("recipient_name", None)
        resolved = await _resolve_twitter_recipient_name(
            user,
            dm_conversation_id=params.get("dm_conversation_id"),
            participant_id=params.get("participant_id"),
        )
        if resolved:
            params["recipient_name"] = resolved

    async def execute(
        self,
        params: dict,
        user: dict,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Send a Twitter DM using the user's OAuth token."""
        if not get_user_twitter_oauth(user):
            raise RuntimeError(
                "Twitter not connected. Please connect Twitter via Settings > Data Connections."
            )

        message_text = params["message"]
        dm_conversation_id = params.get("dm_conversation_id")
        participant_id = params.get("participant_id")

        if dm_conversation_id:
            # Send to existing conversation
            path = f"dm_conversations/{dm_conversation_id}/messages"
        else:
            # Create new DM or add to existing one-to-one conversation
            path = f"dm_conversations/with/{participant_id}/messages"

        try:
            resp = await twitter_request(
                user, "POST", path, json_body={"text": message_text},
            )
        except TwitterAuthError as exc:
            raise RuntimeError(str(exc)) from exc

        if not resp.is_success:
            raise RuntimeError(
                f"Failed to send Twitter DM: {resp.status_code} {resp.text}"
            )

        result_data = resp.json()
        data = result_data.get("data", {})
        return {
            "success": True,
            "dm_conversation_id": data.get("dm_conversation_id", dm_conversation_id or ""),
            "dm_event_id": data.get("dm_event_id", ""),
        }
