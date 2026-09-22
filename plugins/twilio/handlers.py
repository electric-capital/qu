"""TwilioSendSmsHandler: the approval-gated text-to-anyone action request.

Every text to a number other than the user's own verified one goes
through this handler, so a human always sees the recipient and the exact
body before anything is sent -- regardless of the admin's trusted-channel
switch (which only governs the no-approval ``twilio_send_self_sms`` tool
in plugins/twilio/tools.py).
"""

from __future__ import annotations

import logging

from chat.action_request_types.base import ActionRequestHandler
from chat.action_request_types._param_validation import reject_unknown_params

from plugins.twilio.upstream import (
    MAX_SMS_BODY_LENGTH,
    TwilioError,
    load_twilio_config,
    normalize_phone_number,
    send_sms,
)

logger = logging.getLogger(__name__)

_ALLOWED_PARAMS = frozenset({"to", "body", "recipient_name"})

_MAX_RECIPIENT_NAME_LENGTH = 120


class TwilioSendSmsHandler(ActionRequestHandler):
    """Send an SMS to any phone number via the admin-configured Twilio sender."""

    @property
    def type_name(self) -> str:
        return "twilio_send_sms"

    @property
    def display_name(self) -> str:
        return "Send SMS"

    @property
    def preview_fields(self) -> list[str]:
        return ["recipient_name", "to", "body"]

    @property
    def approve_label(self) -> str:
        return "Send"

    @property
    def resolved_label(self) -> str:
        return "Sent"

    def summary_snippet(self, params: dict) -> str:
        to = params.get("recipient_name") or params.get("to") or ""
        body = str(params.get("body") or "")
        return f"{to}: {body}" if to else body

    async def render_preview(self, params: dict, user: dict | None = None) -> list[dict]:
        to = params.get("to", "")
        name = params.get("recipient_name")
        return [
            {"key": "To", "value": f"{name} ({to})" if name else to},
            {"key": "Message", "value": params.get("body", "")},
        ]

    def validate_params(self, params: dict) -> dict:
        reject_unknown_params(self.type_name, params, _ALLOWED_PARAMS)

        to = params.get("to")
        if not to or not str(to).strip():
            raise ValueError("Missing required parameter: to (E.164 phone number, e.g. +15551234567)")
        try:
            to_normalized = normalize_phone_number(str(to))
        except ValueError as exc:
            raise ValueError(f"Invalid `to`: {exc}") from exc

        body = params.get("body")
        if not body or not str(body).strip():
            raise ValueError("Missing required parameter: body")
        body_str = str(body).strip()
        if len(body_str) > MAX_SMS_BODY_LENGTH:
            raise ValueError(f"body exceeds the {MAX_SMS_BODY_LENGTH}-character SMS limit")

        validated: dict = {"to": to_normalized, "body": body_str}

        recipient_name = params.get("recipient_name")
        if recipient_name is not None and str(recipient_name).strip():
            name = str(recipient_name).strip()
            if len(name) > _MAX_RECIPIENT_NAME_LENGTH:
                raise ValueError(
                    f"recipient_name exceeds {_MAX_RECIPIENT_NAME_LENGTH} characters"
                )
            validated["recipient_name"] = name
        return validated

    async def execute(
        self,
        params: dict,
        user: dict,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        from fastapi import HTTPException

        try:
            config = load_twilio_config()
        except HTTPException as exc:
            raise RuntimeError(str(exc.detail)) from exc
        try:
            result = await send_sms(params["to"], params["body"], config)
        except TwilioError as exc:
            raise RuntimeError(f"Failed to send SMS: {exc}") from exc

        logger.info("[twilio_send_sms] sent (user=%s)", user.get("email"))
        return {
            "success": True,
            "to": params["to"],
            "message_sid": result.get("sid", ""),
            "status": result.get("status", ""),
        }
