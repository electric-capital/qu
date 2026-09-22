"""get_current_time and set_conversation_name handlers.
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, available_timezones


def _handle_get_current_time(user_timezone: str) -> str:
    """Execute the get_current_time tool locally."""
    import time as _time

    now_utc = datetime.now(timezone.utc)
    unix_timestamp = _time.time()

    result = {
        "unix_timestamp": int(unix_timestamp),
        "utc": {
            "iso8601": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "readable": now_utc.strftime("%A, %B %d, %Y %H:%M:%S UTC"),
        },
    }

    if user_timezone and user_timezone in available_timezones():
        tz = ZoneInfo(user_timezone)
        now_local = now_utc.astimezone(tz)
        utc_offset = now_local.strftime("%z")
        utc_offset_formatted = f"{utc_offset[:3]}:{utc_offset[3:]}"
        result["user_timezone"] = {
            "timezone": user_timezone,
            "iso8601": now_local.strftime("%Y-%m-%dT%H:%M:%S") + utc_offset_formatted,
            "readable": now_local.strftime("%A, %B %d, %Y %I:%M:%S %p %Z"),
            "utc_offset": utc_offset_formatted,
        }
    else:
        result["user_timezone"] = {
            "timezone": user_timezone or "unknown",
            "error": "Invalid or unrecognized timezone. Showing UTC only.",
        }

    return json.dumps(result)



# ---------------------------------------------------------------------------
# Conversation name handler
# ---------------------------------------------------------------------------

async def _handle_set_conversation_name(user_id: int, conversation_id: str, name: str) -> str:
    """Set a custom display name on the conversation if one is not already set.

    The name is only applied when ``custom_name`` is currently ``None``.
    If the user (or a previous automatic call) has already set a name, the
    request is acknowledged but the existing name is preserved.

    Args:
        user_id: Authenticated user's integer ID.
        conversation_id: Conversation UUID.
        name: Short topic summary to use as the conversation name.

    Returns:
        JSON string with the result (success, already_set, or error).
    """
    from db.conversation_store import (
        get_conversation_meta,
        rename_conversation,
        MAX_CONVERSATION_NAME_LENGTH,
    )

    try:
        name = (name or "").strip()
        if not name:
            return json.dumps({"error": "Name cannot be empty"})

        # Truncate to the database column limit
        if len(name) > MAX_CONVERSATION_NAME_LENGTH:
            name = name[:MAX_CONVERSATION_NAME_LENGTH]

        meta = await get_conversation_meta(user_id, conversation_id)
        if meta is None:
            return json.dumps({"error": "Conversation not found"})

        if meta.get("custom_name") is not None:
            return json.dumps({
                "status": "already_set",
                "current_name": meta["custom_name"],
                "name_set": False,
            })

        await rename_conversation(user_id, conversation_id, name)
        return json.dumps({"status": "ok", "name": name, "name_set": True})
    except Exception as exc:
        return json.dumps({"error": f"Failed to set conversation name: {exc}"})

