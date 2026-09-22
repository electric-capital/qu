"""Quest-managed Gmail label configuration helpers.

Users configure a list of label names in Settings > Gmail, stored in the
``users.settings`` JSON blob under the ``gmail_labels`` key. Each configured
name corresponds to a nested Gmail label ``[Quest]/<name>`` (same parent
label group as the ``[Quest]/archived`` label applied by
``archive_gmail_message``). The LLM may only add/remove labels from this
user-configured list via the ``modify_gmail_labels`` tool.

Shared between the settings API (``chat/routes/user.py``), which validates
the list on write, and the tool handlers (``chat/gemini_api/tool_handlers/gmail_labels.py``),
which resolve configured names to Gmail label IDs.
"""

# Parent label grouping all Quest-managed labels in Gmail. Must match
# _QUEST_PARENT_LABEL_NAME in chat/gemini_api/tool_handlers/gmail_labels.py (the
# archive handler predates this module).
QUEST_PARENT_LABEL_NAME = "[Quest]"

GMAIL_LABELS_SETTINGS_KEY = "gmail_labels"

MAX_GMAIL_LABELS = 50
MAX_GMAIL_LABEL_NAME_LENGTH = 80


def full_quest_label_name(name: str) -> str:
    """Return the full Gmail label name for a configured label name."""
    return f"{QUEST_PARENT_LABEL_NAME}/{name}"


def get_configured_gmail_labels(settings: dict | None) -> list[str]:
    """Return the user's configured Quest label names from a settings dict.

    Tolerates a missing/malformed value (returns an empty list) so callers
    never crash on hand-edited or legacy settings blobs.
    """
    raw = (settings or {}).get(GMAIL_LABELS_SETTINGS_KEY)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def validate_gmail_label_names(labels) -> list[str]:
    """Validate and normalize a user-supplied Quest label name list.

    Names are stripped of surrounding whitespace and deduplicated
    case-insensitively (Gmail label names are unique case-insensitively),
    preserving first occurrence and original casing.

    Returns:
        The cleaned list of label names.

    Raises:
        ValueError: With a user-facing message when the list is invalid.
    """
    if not isinstance(labels, list):
        raise ValueError("gmail_labels must be a list of label names.")
    if len(labels) > MAX_GMAIL_LABELS:
        raise ValueError(f"At most {MAX_GMAIL_LABELS} labels are allowed.")

    cleaned: list[str] = []
    seen_lower: set[str] = set()
    for item in labels:
        if not isinstance(item, str):
            raise ValueError("Each label name must be a string.")
        name = item.strip()
        if not name:
            raise ValueError("Label names cannot be empty.")
        if len(name) > MAX_GMAIL_LABEL_NAME_LENGTH:
            raise ValueError(
                f"Label name '{name[:40]}...' is too long "
                f"(max {MAX_GMAIL_LABEL_NAME_LENGTH} characters)."
            )
        if "/" in name:
            raise ValueError(
                f"Label name '{name}' cannot contain '/' -- labels are "
                f"automatically nested under '{QUEST_PARENT_LABEL_NAME}/'."
            )
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
            raise ValueError("Label names cannot contain control characters.")
        if name.lower() in seen_lower:
            continue
        seen_lower.add(name.lower())
        cleaned.append(name)
    return cleaned
