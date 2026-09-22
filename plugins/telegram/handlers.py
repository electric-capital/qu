"""SendTelegramMessageHandler: the approval-gated ``send_telegram_message`` action request.

The type name predates the plugin packaging and is persisted in old
``action_requests`` rows, so the manifest lists it in
``unprefixed_action_types``.
"""

from __future__ import annotations

import html
import logging
import re

from chat.action_request_types.base import ActionRequestHandler
from chat.action_request_types._param_validation import reject_unknown_params

from plugins.telegram.upstream import (
    TelegramClientManager,
    get_telegram_session,
    resolve_dialog_name,
)

logger = logging.getLogger(__name__)

TYPE_NAME = "send_telegram_message"

# `dialog_name` is server-injected after validation by the handler's
# enrich_params_for_preview hook; not model-supplied.
_ALLOWED_PARAMS = frozenset({"dialog_id", "message"})

# Telegram peer ids are signed integers serialised with conventional
# sign-prefixing (positive=user, small-negative=basic group, large
# `-100`-prefixed negative=supergroup/channel). When the model passes a
# string we require it to be a bare optional-sign digit run so values
# like `@alice`, `t.me/alice`, `+15551234567`, `Cabc12345`, or a
# whitespace-padded string fail the format check rather than slipping
# past `int()` (which would normalise `"  42  "` to 42) or surfacing as
# an opaque Telethon `PeerIdInvalid` inside execute().
_DIALOG_ID_STR_RE = re.compile(r"^-?[0-9]+$")

# Generous upper bound on |dialog_id|. Telegram peer ids do not exceed
# 2**53 in practice; we use 10**15 (slightly under 2**50) so a 17-digit
# id misrouted from Twitter / Slack / a phone number is rejected while
# legitimate supergroup ids (around -100..., 13 digits) remain accepted.
_DIALOG_ID_MAX_ABS = 10**15

MESSAGE_MAX_LENGTH = 4096


def _truncate_for_error(value: str, limit: int = 100) -> str:
    """Truncate ``value`` for inclusion in a ValueError message.

    Keeps error strings short so the model is not handed back a giant
    URL or pasted blob it should not be echoing.
    """
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


class SendTelegramMessageHandler(ActionRequestHandler):
    """Send a Telegram message to any dialog (user, group, or channel).

    Uses the user's existing Telethon session via TelegramClientManager
    so the message is sent as the authenticated user.
    """

    @property
    def type_name(self) -> str:
        return TYPE_NAME

    @property
    def display_name(self) -> str:
        return "Send Telegram Message"

    @property
    def preview_fields(self) -> list[str]:
        return ["dialog_id", "message"]

    @property
    def approve_label(self) -> str:
        return "Send"

    @property
    def resolved_label(self) -> str:
        return "Sent"

    def summary_snippet(self, params: dict) -> str:
        return str(params.get("message") or "")

    async def render_preview(self, params: dict, user: dict | None = None) -> list[dict]:
        to_value = params.get("dialog_name") or str(params.get("dialog_id", "Unknown dialog"))
        return [
            {"key": "To", "value": to_value},
            {"key": "Message", "value": params.get("message", "")},
        ]

    def validate_params(self, params: dict) -> dict:
        reject_unknown_params(TYPE_NAME, params, _ALLOWED_PARAMS)

        dialog_id = params.get("dialog_id")
        message = params.get("message")

        if dialog_id is None or (isinstance(dialog_id, str) and not dialog_id.strip()):
            raise ValueError("Missing required parameter: dialog_id (Telegram dialog ID)")

        # Format-validate dialog_id up front. The contract is "Python int
        # or a digit string with optional leading '-'": we explicitly
        # reject bool (which subclasses int and would silently become 0
        # or 1), float (`1.0` -> `int` -> `1` is a model-error symptom),
        # and any non-string non-int type. Whitespace-padded strings and
        # `@username` / `t.me/...` shapes fail the regex.
        if isinstance(dialog_id, bool) or not isinstance(dialog_id, (int, str)):
            raise ValueError(
                f"dialog_id must be a numeric Telegram dialog id "
                f"(int or digit string with optional leading '-'); got "
                f"{_truncate_for_error(repr(dialog_id))}"
            )
        if isinstance(dialog_id, str):
            if not _DIALOG_ID_STR_RE.match(dialog_id):
                raise ValueError(
                    f"dialog_id must be a numeric Telegram dialog id "
                    f"(int or digit string with optional leading '-'); got "
                    f"{_truncate_for_error(dialog_id)!r}"
                )
            dialog_id_int = int(dialog_id)
        else:
            dialog_id_int = dialog_id

        if dialog_id_int == 0:
            raise ValueError("dialog_id must be a non-zero Telegram peer id; got 0")
        if dialog_id_int == -100:
            raise ValueError(
                "dialog_id -100 is the supergroup/channel marker prefix, "
                "not a full id; the full form is -100<digits>"
            )
        if abs(dialog_id_int) > _DIALOG_ID_MAX_ABS:
            raise ValueError(
                f"dialog_id {dialog_id_int} is outside the plausible "
                f"Telegram peer-id range (|id| <= 10^15); double-check the "
                f"value via telegram_list_dialogs or telegram_list_contacts"
            )

        if not message or not str(message).strip():
            raise ValueError("Missing required parameter: message")
        message_str = str(message).strip()
        if len(message_str) > MESSAGE_MAX_LENGTH:
            raise ValueError(f"Message exceeds Telegram's {MESSAGE_MAX_LENGTH} character limit")

        return {
            "dialog_id": dialog_id_int,
            "message": message_str,
        }

    async def enrich_params_for_preview(self, params: dict, user: dict) -> None:
        """Resolve the dialog id to a display name for the approval card.

        The label is always derived from ``dialog_id`` so the card can
        never name a different peer than execute() sends to; any
        pre-existing dialog_name is discarded. Never raises -- on
        resolution failure the card falls back to the raw id.
        """
        params.pop("dialog_name", None)
        dialog_name = await resolve_dialog_name(params["dialog_id"], user)
        if dialog_name:
            params["dialog_name"] = dialog_name

    async def execute(
        self,
        params: dict,
        user: dict,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Send a message using the user's Telegram session."""
        if not get_telegram_session(user):
            raise RuntimeError(
                "Telegram not connected. Please connect Telegram via Settings > Data Connections."
            )

        try:
            client = await TelegramClientManager.get_instance().get_client(user)
        except Exception as exc:
            # HTTPException from TelegramClientManager -- re-raise as RuntimeError
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                detail = exc.detail
                if isinstance(detail, dict):
                    detail = detail.get("message", str(detail))
                raise RuntimeError(str(detail)) from exc
            raise

        # Escape message body for HTML parse mode, then append italic attribution
        escaped_body = html.escape(params["message"])
        message_text = escaped_body + "\n\n<i>\U0001f916 Sent with AI assistance</i>"

        sent_message = await client.send_message(
            entity=params["dialog_id"], message=message_text, parse_mode="html",
        )

        return {
            "success": True,
            "dialog_id": params["dialog_id"],
            "message_id": sent_message.id,
        }
