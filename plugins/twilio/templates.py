"""Pre-written SMS message ("template") helpers (plugin module).

Users author a list of ``{"name", "body"}`` messages in Settings > SMS
Messages (the FE section calls the plugin's ``/auth/twilio/templates``
routes in plugins/twilio/verify.py). The list is stored in the
``users.settings`` JSON blob under :data:`TEMPLATES_SETTINGS_KEY`, the
same place Gmail's Quest-managed label list lives, so it survives a
phone-number disconnect/reconnect.

When the admin has NOT marked Twilio as a trusted channel, the
``twilio_send_self_sms`` tool may only send one of these messages
verbatim -- no free-form text, no placeholders -- so the list doubles as
the whole set of things Quest can ever text the user unprompted.
"""

from __future__ import annotations

from plugins.twilio.upstream import MAX_SMS_BODY_LENGTH

TEMPLATES_SETTINGS_KEY = "twilio_sms_templates"

MAX_TEMPLATES = 50
MAX_TEMPLATE_NAME_LENGTH = 60


def get_configured_templates(settings: dict | None) -> list[dict]:
    """Return the user's templates from a settings dict.

    Tolerates a missing/malformed value (returns an empty list) and drops
    malformed rows so callers never crash on a hand-edited blob.
    """
    raw = (settings or {}).get(TEMPLATES_SETTINGS_KEY)
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        body = item.get("body")
        if isinstance(name, str) and name.strip() and isinstance(body, str) and body.strip():
            out.append({"name": name, "body": body})
    return out


def find_template(templates: list[dict], name: str) -> dict | None:
    """Case-insensitive lookup of a template by name."""
    wanted = name.strip().lower()
    for template in templates:
        if template["name"].strip().lower() == wanted:
            return template
    return None


def _has_control_chars(value: str, allow_newlines: bool) -> bool:
    for ch in value:
        code = ord(ch)
        if code == 127 or (code < 32 and not (allow_newlines and ch in "\n\r\t")):
            return True
    return False


def validate_templates(templates) -> list[dict]:
    """Validate and normalize a user-supplied template list.

    Names are stripped and must be unique case-insensitively; bodies are
    stripped and capped at Twilio's single-message limit. Returns the
    cleaned ``[{"name", "body"}]`` list.

    Raises:
        ValueError: With a user-facing message when the list is invalid.
    """
    if not isinstance(templates, list):
        raise ValueError("templates must be a list of {name, body} objects.")
    if len(templates) > MAX_TEMPLATES:
        raise ValueError(f"At most {MAX_TEMPLATES} messages are allowed.")

    cleaned: list[dict] = []
    seen_lower: set[str] = set()
    for index, item in enumerate(templates):
        if not isinstance(item, dict):
            raise ValueError(f"Message #{index + 1} must be an object with name and body.")
        unknown = sorted(set(item) - {"name", "body"})
        if unknown:
            raise ValueError(
                f"Message #{index + 1} has unknown fields: {unknown}."
            )
        name = item.get("name")
        body = item.get("body")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Message #{index + 1} needs a name.")
        if not isinstance(body, str) or not body.strip():
            raise ValueError(f"Message '{name.strip()[:40]}' needs a body.")
        name = name.strip()
        body = body.strip()
        if len(name) > MAX_TEMPLATE_NAME_LENGTH:
            raise ValueError(
                f"Message name '{name[:40]}...' is too long "
                f"(max {MAX_TEMPLATE_NAME_LENGTH} characters)."
            )
        if _has_control_chars(name, allow_newlines=False):
            raise ValueError("Message names cannot contain control characters.")
        if len(body) > MAX_SMS_BODY_LENGTH:
            raise ValueError(
                f"Message '{name}' is too long (max {MAX_SMS_BODY_LENGTH} characters)."
            )
        if _has_control_chars(body, allow_newlines=True):
            raise ValueError(
                f"Message '{name}' cannot contain control characters."
            )
        lowered = name.lower()
        if lowered in seen_lower:
            raise ValueError(f"Duplicate message name '{name}'.")
        seen_lower.add(lowered)
        cleaned.append({"name": name, "body": body})
    return cleaned
