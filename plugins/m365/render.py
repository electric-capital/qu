"""Markdown rendering for Microsoft Graph (Outlook) mail messages.

Mirrors the Gmail Simple tools' LLM-facing output format (see
``api/gmail/helpers.py``): a labelled header block, an HTML-to-markdown
body with optional long-URL replacement, and an attachments section with
each ``attachmentId`` recoverable as a labelled line. The pure conversion
helpers (HTML-to-markdown pipeline, URL replacement, per-conversation URL cache)
are shared with the Gmail integration -- they are email-generic, and
sharing them keeps the two integrations' output byte-for-byte consistent
in style.
"""

from api.gmail.helpers import (
    _cache_url_mapping,
    _fence_for_html,
    _sanitize_header_value,
    clean_email_markdown,
    convert_html_to_markdown,
    replace_urls_with_identifiers,
)


def format_email_address(recipient: dict | None) -> str:
    """Format a Graph ``recipient`` object as ``Name <address>``."""
    email = (recipient or {}).get("emailAddress") or {}
    name = _sanitize_header_value(email.get("name") or "")
    address = _sanitize_header_value(email.get("address") or "")
    if name and address and name != address:
        return f"{name} <{address}>"
    return address or name or "(unknown)"


def format_recipients(recipients: list | None) -> str:
    """Format a Graph recipient list as a comma-separated header value."""
    return ", ".join(format_email_address(r) for r in (recipients or []))


def render_message_markdown(
    message: dict,
    include_html: bool = False,
    replace_urls: bool = True,
    conversation_id: str | None = None,
) -> str:
    """Render a Graph mail message as a markdown document for the LLM.

    Args:
        message: Raw Graph message object (with ``body`` and, when
            requested via ``$expand``, ``attachments`` metadata).
        include_html: If True, emit the raw HTML body in a fenced ``html``
            block instead of the markdown conversion.
        replace_urls: If True (default), replace long URLs in HTML bodies
            with short numeric identifiers. No effect for text bodies.
        conversation_id: Conversation ID used to key the URL-mapping
            cache. Required for URL replacement to actually run; if
            omitted, URLs are preserved as-is.
    """
    graph_message_id = message.get("id", "") or ""
    conversation_ref = message.get("conversationId", "") or ""
    internet_message_id = _sanitize_header_value(
        message.get("internetMessageId") or ""
    )
    subject = _sanitize_header_value(message.get("subject") or "")
    date = message.get("receivedDateTime") or message.get("sentDateTime") or ""
    categories = [c for c in (message.get("categories") or []) if c]
    is_read = message.get("isRead")

    body = message.get("body") or {}
    content_type = (body.get("contentType") or "").lower()
    content = body.get("content") or ""
    html_body = content if content_type == "html" else ""
    plain_body = content if content_type != "html" else ""

    # --- Body + optional replaced-url count ----------------------------
    replaced_url_count: int | None = None
    if not html_body and not plain_body.strip():
        body_section = "## Body\n\n_(empty body)_"
    elif include_html and html_body:
        fence = _fence_for_html(html_body)
        body_section = f"## Body (HTML)\n\n{fence}html\n{html_body}\n{fence}"
    elif html_body:
        markdown_body = convert_html_to_markdown(html_body)
        markdown_body = clean_email_markdown(markdown_body)
        if replace_urls and conversation_id:
            replaced_body, url_mapping = replace_urls_with_identifiers(markdown_body)
            if url_mapping:
                _cache_url_mapping(conversation_id, graph_message_id, url_mapping)
                markdown_body = replaced_body
                replaced_url_count = len(url_mapping)
        body_section = (
            f"## Body\n\n{markdown_body}" if markdown_body
            else "## Body\n\n_(empty body)_"
        )
    else:
        # Plain-text body. Do NOT fence -- plain bodies can contain
        # backticks, and fencing could corrupt the output.
        body_section = f"## Body\n\n{plain_body}"

    # --- Header block --------------------------------------------------
    header_lines = [f"Subject: {subject if subject else '(no subject)'}"]
    from_value = format_email_address(message.get("from"))
    to_value = format_recipients(message.get("toRecipients"))
    cc_value = format_recipients(message.get("ccRecipients"))
    bcc_value = format_recipients(message.get("bccRecipients"))
    header_lines.append(f"From: {from_value}")
    header_lines.append(f"To: {to_value if to_value else '(unknown)'}")
    if cc_value:
        header_lines.append(f"Cc: {cc_value}")
    if bcc_value:
        header_lines.append(f"Bcc: {bcc_value}")
    header_lines.append(f"Date: {date if date else '(unknown)'}")
    header_lines.append(
        f"Message-ID: {internet_message_id if internet_message_id else '(none)'}"
    )
    header_lines.append(f"Graph Message ID: {graph_message_id}")
    header_lines.append(f"Conversation ID: {conversation_ref}")
    header_lines.append(
        f"Categories: {', '.join(categories) if categories else '(none)'}"
    )
    if is_read is not None:
        header_lines.append(f"Read: {'yes' if is_read else 'no'}")
    if replaced_url_count is not None:
        header_lines.append(f"Replaced URL count: {replaced_url_count}")
    header_block = "\n".join(header_lines)

    # --- Attachments section ------------------------------------------
    attachments = message.get("attachments") or []
    if attachments:
        attachment_lines = ["## Attachments", ""]
        for att in attachments:
            name = _sanitize_header_value(att.get("name") or "") or "(unnamed)"
            mime_type = att.get("contentType") or "application/octet-stream"
            size = att.get("size", 0)
            try:
                size_str = f"{int(size):,}"
            except (TypeError, ValueError):
                size_str = str(size)
            inline_note = ", inline" if att.get("isInline") else ""
            attachment_lines.append(
                f"- **{name}** -- `{mime_type}`, {size_str} bytes{inline_note}, "
                f"attachmentId: `{att.get('id', '')}`"
            )
        attachments_section = "\n".join(attachment_lines)
    else:
        attachments_section = "## Attachments\n\n_No attachments._"

    return (
        f"{header_block}\n\n---\n\n{body_section}\n\n---\n\n{attachments_section}"
    )


def render_batch_markdown(entries: list[dict]) -> str:
    """Render a multi-message fetch as one markdown document.

    Same shape as the Gmail batch renderer: an ``# Outlook messages
    batch`` heading, a counts line, an optional ``## Batch errors``
    section emitted before the per-message sections, and ``===``
    separators between messages. Entry order is preserved.
    """
    successes = [e for e in entries if e.get("ok")]
    errors = [e for e in entries if not e.get("ok")]

    lines = [
        "# Outlook messages batch",
        "",
        f"Fetched {len(successes)} message(s). {len(errors)} error(s).",
    ]

    if errors:
        lines.append("")
        lines.append("## Batch errors")
        lines.append("")
        for entry in errors:
            entry_id = entry.get("id", "")
            code = entry.get("error_code", "fetch_error")
            msg = entry.get("error_message", "")
            lines.append(f"- **{entry_id}** -- `{code}`: {msg}")

    document = "\n".join(lines)

    if successes:
        document = document + "\n\n===\n\n" + "\n\n===\n\n".join(
            entry.get("markdown", "") for entry in successes
        )

    return document


def simplify_folder(folder: dict) -> dict:
    """Convert a Graph mailFolder to LLM-friendly form."""
    return {
        "id": folder.get("id"),
        "name": folder.get("displayName"),
        "parent_folder_id": folder.get("parentFolderId"),
        "child_folder_count": folder.get("childFolderCount"),
        "unread_item_count": folder.get("unreadItemCount"),
        "total_item_count": folder.get("totalItemCount"),
    }
