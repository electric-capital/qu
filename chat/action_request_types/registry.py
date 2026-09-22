"""Action request handler registry."""

from typing import Optional

from chat.action_request_types.base import ActionRequestHandler


_REGISTRY: dict[str, ActionRequestHandler] = {}


def register_handler(handler: ActionRequestHandler) -> None:
    """Register an action request handler.

    ``type_name`` may be a db.models.ActionRequestType member (core
    handlers) or a plain string (plugin handlers) -- the registry is keyed
    by the string value either way. StrEnum members ARE strings, so
    ``str()`` yields the enum value.
    """
    key = str(handler.type_name)
    if key in _REGISTRY:
        raise ValueError(f"Action request handler already registered: {key!r}")
    _REGISTRY[key] = handler


def get_handler(type_name: str) -> Optional[ActionRequestHandler]:
    """Get the handler for a given request type string."""
    return _REGISTRY.get(type_name)


def get_all_type_names() -> list[str]:
    """Return all registered type name strings."""
    return list(_REGISTRY.keys())


# Collapsed-card summary snippets are truncated server-side so the FE can
# render them verbatim.
_SNIPPET_MAX_LEN = 80


def get_summary_snippet(request_type: str, params: dict) -> str:
    """Truncated collapsed-card snippet for a request, '' when unknown.

    A handler bug (e.g. legacy params shapes in old chat history) must
    never break rendering, so exceptions degrade to no snippet.
    """
    handler = get_handler(request_type)
    if not handler:
        return ""
    try:
        snippet = handler.summary_snippet(dict(params or {}))
    except Exception:
        return ""
    if not isinstance(snippet, str):
        return ""
    if len(snippet) > _SNIPPET_MAX_LEN:
        snippet = snippet[:_SNIPPET_MAX_LEN] + "..."
    return snippet


async def get_preview_for_request(request_type: str, params: dict, user: dict | None = None) -> dict:
    """Get preview data for an action request.

    Looks up the handler for the given request_type and returns a dict with
    preview_fields, display_name, approve_label, resolved_label, and
    summary_snippet. Falls back gracefully for unknown types.

    Args:
        request_type: The action request type string.
        params: Validated parameter dict for the action request.
        user: Authenticated user dict, passed through to render_preview for
              handlers that need to resolve external data.
    """
    handler = get_handler(request_type)
    if handler:
        return {
            "preview_fields": await handler.render_preview(params, user),
            "display_name": handler.display_name,
            "approve_label": handler.approve_label,
            "resolved_label": handler.resolved_label,
            "summary_snippet": get_summary_snippet(request_type, params),
        }
    # Fallback for unknown types
    import json
    return {
        "preview_fields": [{"key": "Parameters", "value": json.dumps(params, default=str)}],
        "display_name": request_type.replace("_", " ").title(),
        "approve_label": "Approve",
        "resolved_label": "Sent",
        "summary_snippet": "",
    }
