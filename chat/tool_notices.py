"""Lightweight data types for passing advisory notices from route handlers.

This module has no dependencies on the rest of the chat package, so it can
be safely imported by both ``api.*`` modules and ``chat.route_dispatch``
without circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResultWithNotices:
    """Wrapper that route handlers can return to attach advisory notices.

    When a handler returns this instead of a plain dict/str, the route
    dispatch layer extracts the ``notices`` and passes them back to the
    tool dispatch layer, which converts them into text extra_parts
    appended alongside the tool result.

    Attributes:
        result: The actual response data (dict, list, str, etc.).
        notices: Human-readable advisory strings for the LLM
            (e.g., "Your requested limit of 100 was capped to 50.").
    """
    result: Any
    notices: list[str] = field(default_factory=list)
