"""Per-conversation flags: a small, extensible registry of opt-in behaviors.

Flags are set at the *start* of a conversation (on the first user message) and
persisted onto the ``Conversation`` row as a JSON array of enabled flag names.
There is no UI today: a flag is activated by a magic first line in the first
user message of the form::

    %%flags[nested_subagents]

(comma-separated list inside the brackets). The line is parsed by
:func:`parse_flags_line`, the recognized flags are persisted, and the line is
stripped from the message text before it reaches the model and before it is
persisted to ``chat_history.json``.

This module is intentionally import-light (regex + stdlib only) so it can be
reused by both the WebSocket parsing layer (``chat/realtime/socket.py``) and the
conversation loop (``chat/gemini_api/conversation.py``) without pulling in the
LLM stack.

Adding a future flag is a one-line change: define a constant, add it to
``KNOWN_FLAGS``, and wire up a consumer. The storage column and the parser need
no change.
"""

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known flag names
# ---------------------------------------------------------------------------

# Lets 1st-level sub-agents spawn their own 2nd-level sub-agents (restricted to
# Haiku / Gemini Flash Lite, which cannot spawn any further). See
# docs/architecture/gemini-api.md and chat/gemini_api/sub_agent.py.
FLAG_NESTED_SUBAGENTS = "nested_subagents"

# Lets this conversation propose cross-user subagent runs
# (create_action_request(request_type="run_user_subagent")). Without the flag
# the dispatch arm rejects the request type same-turn. Gates only the CALLER
# side; the subagent conversation it launches needs no flag. See
# docs/architecture/user-subagents.md.
FLAG_USER_SUBAGENTS = "user_subagents"

# The set of flag names the application understands. Tokens in a %%flags[...]
# line that are NOT in this set are silently dropped (and logged) so the magic
# line stays forgiving and forward-compatible.
KNOWN_FLAGS = frozenset({FLAG_NESTED_SUBAGENTS, FLAG_USER_SUBAGENTS})


# ---------------------------------------------------------------------------
# Human-readable display registry
# ---------------------------------------------------------------------------

# Canonical human text for each known flag, keyed by the same machine-name
# constants. This is the source of truth the frontend constant
# (``frontend/src/constants/flags.ts``) hand-mirrors so the composer Flags
# popover can show friendly labels + descriptions instead of raw machine names.
# Adding a future flag therefore touches both this map and the FE constant.
FLAG_LABELS: dict[str, dict[str, str]] = {
    FLAG_NESTED_SUBAGENTS: {
        "label": "Nested sub-agents",
        "description": (
            "Lets 1st-level sub-agents spawn their own 2nd-level sub-agents "
            "(restricted to Haiku / Gemini Flash Lite)."
        ),
    },
    FLAG_USER_SUBAGENTS: {
        "label": "Cross-user subagents",
        "description": (
            "Lets this conversation propose running approved read-only "
            "subagents in other users' accounts (run_user_subagent)."
        ),
    },
}


# ---------------------------------------------------------------------------
# First-message magic-line parsing
# ---------------------------------------------------------------------------

# Matches a first line of the form ``%%flags[a, b, c]`` (the bracket body may be
# empty). Applied to the FIRST line of the message only.
_FLAGS_LINE_RE = re.compile(r"^%%flags\[([^\]]*)\]\s*$")


def parse_flags_line(message: str) -> tuple[list[str], str]:
    """Parse a leading ``%%flags[...]`` magic line out of a message.

    Looks ONLY at the first line. If it matches ``%%flags[<tokens>]``, the
    bracket body is split on commas; each token is trimmed + lowercased and
    validated against :data:`KNOWN_FLAGS`. Unknown tokens are dropped (and
    logged), not errored, so the magic line stays forgiving.

    The matched first line (and a single trailing newline) is stripped from the
    returned message so the remainder is what the user actually intended.

    Args:
        message: The raw user message text.

    Returns:
        ``(recognized_flags, stripped_message)``. When the first line does not
        match, returns ``([], message)`` unchanged. ``recognized_flags`` is
        deduplicated while preserving first-seen order.
    """
    # Split off the first line without disturbing the rest of the body. We keep
    # the remainder verbatim (including its own internal newlines).
    first_line, sep, rest = message.partition("\n")

    match = _FLAGS_LINE_RE.match(first_line)
    if not match:
        return [], message

    raw_tokens = match.group(1)
    recognized: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens.split(","):
        name = token.strip().lower()
        if not name:
            continue
        if name in KNOWN_FLAGS:
            if name not in seen:
                recognized.append(name)
                seen.add(name)
        else:
            logger.info("Ignoring unknown conversation flag '%s' in %%flags line", name)

    # ``partition`` already consumed the single trailing newline (``sep``); the
    # remainder is the message with the magic line removed.
    return recognized, rest


def is_flag_enabled(flags: list[str] | None, name: str) -> bool:
    """NULL-safe check for whether ``name`` is in the conversation's flag set.

    Treats ``None``/missing as "no flags" (empty set), mirroring how the DB
    stores ``NULL`` for a conversation with no flags.
    """
    if not flags:
        return False
    return name in flags
