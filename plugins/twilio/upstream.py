"""Twilio upstream access helpers (plugin module).

Server-side configuration (the admin ``twilio`` credential-store entry:
Account SID, Auth Token, the sending number / Messaging Service SID, and
the **trusted channel** switch) and the per-user verified phone number (a
``user_service_credentials`` row with ``oauth_blob`` JSON -- see
plugins/twilio/verify.py) both resolve here, so the verification router,
the ``twilio_send_self_sms`` tool, and the ``twilio_send_sms`` action
request share one implementation.

There is no OAuth: the user proves ownership of a phone number by typing
a code that Quest texts to it. The plugin still declares an ``oauth``-kind
user connection because that is the manifest shape that lets a plugin
mount its own browser-facing routes under ``/auth/twilio``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"

# Twilio's hard cap for a single Messages resource body (long messages are
# segmented upstream; anything longer is rejected with error 21617).
MAX_SMS_BODY_LENGTH = 1600

_HTTP_TIMEOUT = 30.0

# E.164: '+' then 7-15 digits, first digit non-zero.
_E164_RE = re.compile(r"^\+[1-9][0-9]{6,14}$")
# Twilio Messaging Service SID: "MG" + 32 hex chars.
_MESSAGING_SERVICE_SID_RE = re.compile(r"^MG[0-9a-fA-F]{32}$")
_ACCOUNT_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")

# Characters people paste into phone numbers that carry no information.
_PHONE_NOISE_RE = re.compile(r"[\s().\-]")

MISSING_CREDENTIALS_ERROR = (
    "Twilio is not connected. Verify your phone number via "
    "Settings > Data Connections > Twilio first."
)


class TwilioError(RuntimeError):
    """A Twilio REST call failed (non-2xx or transport error)."""


# ---------------------------------------------------------------------------
# Admin (server-level) configuration
# ---------------------------------------------------------------------------

def load_twilio_config() -> dict:
    """Load the admin Twilio config from the per-service credential store.

    Store reads are fresh on every call so admin edits -- notably flipping
    the trusted-channel switch -- take effect immediately. Raises a 500
    HTTPException when unconfigured (mirrors the other plugins' loaders).
    """
    from fastapi import HTTPException

    from config.service_credentials import read_service_credentials

    stored = read_service_credentials("twilio")
    if stored and twilio_is_configured(stored):
        return stored
    raise HTTPException(
        status_code=500,
        detail=(
            "Twilio credentials not configured. Set the Account SID, Auth "
            "Token, and sending number in Settings > Service Credentials "
            "(admin)."
        ),
    )


def twilio_is_configured(config: dict) -> bool:
    """Admin ``is_configured`` predicate: SID, token, and sender present."""
    return bool(
        config.get("account_sid")
        and config.get("auth_token")
        and config.get("from_number")
    )


def is_trusted_channel(config: dict | None) -> bool:
    """Whether the admin marked Twilio SMS as a trusted channel.

    Off (the default) means free-form texts to the user's own number are
    refused: ``twilio_send_self_sms`` may only send one of the user's
    pre-written templates. Only a JSON ``true`` counts -- the store is
    written by the schema-driven admin endpoint which coerces the bool
    field, but a hand-edited file could hold anything.
    """
    return (config or {}).get("trusted_channel") is True


def validate_twilio_credentials(values: dict) -> dict:
    """Admin-save normalization hook: strip whitespace, check id shapes.

    Raises ValueError (surfaced as a 400 by the generic credentials
    endpoint) for a malformed Account SID or sender. A blank secret is
    left alone -- the generic PUT treats it as "keep the stored value".
    """
    out = {
        key: (value.strip() if isinstance(value, str) else value)
        for key, value in values.items()
    }
    account_sid = out.get("account_sid")
    if account_sid and not _ACCOUNT_SID_RE.match(account_sid):
        raise ValueError(
            "Account SID must look like 'AC' followed by 32 hex characters."
        )
    sender = out.get("from_number")
    if sender:
        if _MESSAGING_SERVICE_SID_RE.match(sender):
            out["from_number"] = sender
        else:
            try:
                out["from_number"] = normalize_phone_number(sender)
            except ValueError:
                raise ValueError(
                    "Sending number must be an E.164 phone number "
                    "(e.g. +15551234567) or a Messaging Service SID (MG...)."
                )
    return out


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

def normalize_phone_number(raw: Any) -> str:
    """Normalize a user-typed phone number to E.164, or raise ValueError.

    Spaces, dots, dashes, and parentheses are dropped; a leading ``00``
    international prefix becomes ``+``. The country code is never guessed
    -- a number without one is rejected so the verification code cannot
    go to a stranger in a different country.
    """
    if not isinstance(raw, str):
        raise ValueError("Phone number must be a string.")
    cleaned = _PHONE_NOISE_RE.sub("", raw.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        raise ValueError(
            "Phone number must include the country code and start with '+', "
            "e.g. +15551234567."
        )
    if not _E164_RE.match(cleaned):
        raise ValueError(
            "Phone number must be in E.164 format: '+' followed by 7-15 "
            "digits, e.g. +15551234567."
        )
    return cleaned


def mask_phone_number(number: str | None) -> str:
    """Render a phone number for display to the model: last 4 digits only."""
    if not number:
        return ""
    digits = number.lstrip("+")
    tail = digits[-4:]
    return "+" + "•" * max(len(digits) - len(tail), 0) + tail


# ---------------------------------------------------------------------------
# Per-user connection (verified phone number)
# ---------------------------------------------------------------------------

def get_user_twilio_blob(user: dict) -> Optional[dict]:
    """The user's stored Twilio blob, or None when no row exists.

    Reads the ``user_service_credentials`` row attached to the user dict
    by db/user_store.py (service key ``twilio``). The blob carries the
    verified ``phone_number`` and, mid-flow, a ``pending`` verification.
    """
    rows = user.get("service_credentials") or {}
    return (rows.get("twilio") or {}).get("oauth_blob")


def get_user_phone_number(user: dict) -> Optional[str]:
    """The user's VERIFIED phone number, or None when not connected."""
    blob = get_user_twilio_blob(user) or {}
    number = blob.get("phone_number")
    return number if isinstance(number, str) and number else None


def twilio_connected(row: dict) -> bool:
    """``UserConnectionSpec.connected`` hook: a verified number is stored."""
    return bool((row.get("oauth_blob") or {}).get("phone_number"))


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _sender_field(config: dict) -> dict[str, str]:
    """The From / MessagingServiceSid form field for the configured sender."""
    sender = str(config.get("from_number") or "")
    if _MESSAGING_SERVICE_SID_RE.match(sender):
        return {"MessagingServiceSid": sender}
    return {"From": sender}


async def send_sms(to: str, body: str, config: dict | None = None) -> dict:
    """Send one SMS through the Twilio Messages API.

    Returns the created Message resource (``sid``, ``status``, ...).
    Raises :class:`TwilioError` on a non-2xx response or transport
    failure, with Twilio's own error message when it sent one.
    """
    if config is None:
        config = load_twilio_config()
    if not body or len(body) > MAX_SMS_BODY_LENGTH:
        raise TwilioError(
            f"SMS body must be 1-{MAX_SMS_BODY_LENGTH} characters."
        )
    account_sid = config["account_sid"]
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"
    form = {"To": to, "Body": body, **_sender_field(config)}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                url, data=form, auth=(account_sid, config["auth_token"]),
            )
    except httpx.HTTPError as exc:
        raise TwilioError(f"Twilio request failed: {exc}") from exc

    if resp.status_code >= 400:
        message = f"HTTP {resp.status_code}"
        try:
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("message"):
                code = payload.get("code")
                message = (
                    f"{payload['message']} (Twilio error {code})"
                    if code else str(payload["message"])
                )
        except ValueError:
            pass
        raise TwilioError(f"Twilio rejected the message: {message}")
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}
