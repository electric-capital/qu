"""Gmail API package -- proxy endpoints, LLM-friendly helpers, and drafts.

This package re-exports all public symbols so that existing imports like
``from api import gmail`` and ``from api.gmail import X`` continue to work.
"""

from .constants import (
    _INVISIBLE_UNICODE_CODEPOINTS,
    _URL_REPLACEMENT_MIN_LENGTH,
)

from .helpers import (
    clean_email_markdown,
    convert_html_to_markdown,
    render_batch_markdown,
    render_message_markdown,
    replace_urls_with_identifiers,
)

from .raw_endpoints import (
    batch_request,
)

from .simple_endpoints import (
    get_label_simple,
    get_message_simple,
    get_messages_batch,
    get_urls_by_identifiers,
    get_urls_for_message,
    list_labels_simple,
)

from .draft_endpoints import (
    _resolve_gmail_attachment,
    create_draft,
    send_email_to_self,
)

from .instructions import get_instructions

__all__ = [
    # Constants
    "_INVISIBLE_UNICODE_CODEPOINTS",
    "_URL_REPLACEMENT_MIN_LENGTH",
    # Helpers (directly imported by tests / external code)
    "clean_email_markdown",
    "convert_html_to_markdown",
    "render_batch_markdown",
    "render_message_markdown",
    "replace_urls_with_identifiers",
    # Raw endpoints
    "batch_request",
    # Simple endpoints
    "get_label_simple",
    "get_message_simple",
    "get_messages_batch",
    "get_urls_by_identifiers",
    "get_urls_for_message",
    "list_labels_simple",
    # Draft endpoints
    "_resolve_gmail_attachment",
    "create_draft",
    "send_email_to_self",
    # Instructions
    "get_instructions",
]
