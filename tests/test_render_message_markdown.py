"""Tests for render_message_markdown() in api.gmail.helpers.

These tests feed Gmail-API-shaped dicts through the renderer and assert on
the shape of the resulting markdown document. They do not hit the live
Gmail API.
"""

import base64

import pytest

from api.gmail import (
    _URL_REPLACEMENT_MIN_LENGTH,
    render_message_markdown,
)


def _b64_url(s: str) -> str:
    """Base64url-encode a string the way Gmail returns message body data."""
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


def _plaintext_message(
    *,
    message_id: str = "msg-123",
    thread_id: str = "thread-123",
    subject: str = "Hello",
    sender: str = "Ada <ada@example.com>",
    to: str = "grace@example.com",
    cc: str = "",
    bcc: str = "",
    date: str = "Mon, 27 Jan 2025 10:00:00 -0800",
    message_id_header: str = "<CAA123@mail.gmail.com>",
    label_ids: list[str] | None = None,
    snippet: str = "Preview snippet.",
    body: str = "Plain text body.",
    attachments: list[dict] | None = None,
) -> dict:
    """Build a Gmail-API-shaped dict with a single text/plain body part."""
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": date},
        {"name": "Message-ID", "value": message_id_header},
    ]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    if bcc:
        headers.append({"name": "Bcc", "value": bcc})

    parts: list[dict] = [
        {
            "mimeType": "text/plain",
            "body": {"data": _b64_url(body)} if body else {},
        }
    ]
    for att in attachments or []:
        parts.append({
            "mimeType": att.get("mimeType", "application/octet-stream"),
            "filename": att["filename"],
            "body": {
                "attachmentId": att["attachmentId"],
                "size": att.get("size", 0),
            },
        })

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": list(label_ids) if label_ids is not None else ["INBOX"],
        "snippet": snippet,
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": headers,
            "parts": parts,
        },
    }


def _html_message(
    *,
    html: str,
    message_id: str = "msg-html",
    thread_id: str = "thread-html",
    subject: str = "HTML subject",
    sender: str = "Bob <bob@example.com>",
    to: str = "grace@example.com",
    date: str = "Tue, 28 Jan 2025 09:00:00 -0800",
    message_id_header: str = "<BBB@mail.gmail.com>",
    label_ids: list[str] | None = None,
    snippet: str = "HTML preview",
) -> dict:
    """Build a Gmail-API-shaped dict with a single text/html body part."""
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": date},
        {"name": "Message-ID", "value": message_id_header},
    ]
    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": list(label_ids) if label_ids is not None else ["INBOX"],
        "snippet": snippet,
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": headers,
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64_url(html)},
                },
            ],
        },
    }


def _make_long_url(seed: str = "a") -> str:
    """Return a URL exceeding the URL-replacement length threshold."""
    padding = seed * (_URL_REPLACEMENT_MIN_LENGTH + 20)
    return f"https://example.com/{padding}"


# ---------------------------------------------------------------------------

def test_plain_text_message_renders_expected_headers_and_body():
    message = _plaintext_message(
        subject="Hello",
        sender="Ada <ada@example.com>",
        to="grace@example.com",
        body="Hi there.\nSecond line.",
    )
    out = render_message_markdown(message)

    assert out.startswith("Subject: Hello\n")
    assert "From: Ada <ada@example.com>" in out
    assert "To: grace@example.com" in out
    assert "Date: Mon, 27 Jan 2025 10:00:00 -0800" in out
    assert "Gmail Message ID: msg-123" in out
    assert "Thread ID: thread-123" in out
    assert "## Body" in out
    assert "Hi there." in out
    assert "Second line." in out
    assert "## Attachments" in out
    assert "_No attachments._" in out
    # Cc/Bcc omitted because empty
    assert "Cc:" not in out
    assert "Bcc:" not in out
    # Subject should not be emitted as a markdown heading
    assert "# Hello" not in out
    # Snippet line is no longer emitted
    assert "Snippet:" not in out
    # No URL-replacement line for plain-text bodies
    assert "Replaced URL count" not in out


def test_html_message_converts_to_markdown_with_url_replacement(tmp_path, monkeypatch):
    # Isolate the URL cache to a temporary directory so tests don't pollute state.
    import chat.storage as storage_mod
    monkeypatch.setattr(storage_mod, "CHATS_DIR", tmp_path)

    u1 = _make_long_url("a")
    u2 = _make_long_url("b")
    html = (
        f"<html><body><p>See <a href='{u1}'>first link</a> and "
        f"<a href='{u2}'>second link</a>.</p></body></html>"
    )
    message = _html_message(html=html, message_id="html-urls")
    out = render_message_markdown(
        message, replace_urls=True, conversation_id="conv-xyz"
    )

    assert "(#1#)" in out
    assert "(#2#)" in out
    assert u1 not in out
    assert u2 not in out
    assert "Replaced URL count: 2" in out


def test_html_message_without_conversation_id_keeps_urls(tmp_path, monkeypatch):
    import chat.storage as storage_mod
    monkeypatch.setattr(storage_mod, "CHATS_DIR", tmp_path)

    u1 = _make_long_url("c")
    html = f"<html><body><p><a href='{u1}'>link</a></p></body></html>"
    message = _html_message(html=html)
    out = render_message_markdown(
        message, replace_urls=True, conversation_id=None
    )

    assert u1 in out
    assert "Replaced URL count" not in out


def test_include_html_emits_fenced_html_block():
    html = "<html><h1>Heading</h1><p>Body</p></html>"
    message = _html_message(html=html)
    out = render_message_markdown(message, include_html=True)

    assert "## Body (HTML)" in out
    # Default fence is three backticks, language `html`
    assert "```html" in out
    assert html in out
    # The fence closes
    assert out.rstrip().endswith("```") or "\n```\n\n---" in out


def test_include_html_widens_fence_when_body_contains_triple_backticks():
    html = "<p>Inline code fence ``` inside body</p>"
    message = _html_message(html=html)
    out = render_message_markdown(message, include_html=True)

    # The fence must be longer than any backtick run inside the HTML body.
    assert "````html" in out or "`````html" in out
    assert html in out


def test_attachments_list_preserves_ids_and_sizes():
    message = _plaintext_message(
        body="Body",
        attachments=[
            {
                "filename": "report.pdf",
                "mimeType": "application/pdf",
                "size": 12345,
                "attachmentId": "ANGjd_report_id",
            },
            {
                "filename": "image.png",
                "mimeType": "image/png",
                "size": 8910,
                "attachmentId": "ANGjd_image_id",
            },
        ],
    )
    out = render_message_markdown(message)

    assert "## Attachments" in out
    assert "**report.pdf**" in out
    assert "`application/pdf`" in out
    assert "12,345 bytes" in out
    assert "attachmentId: `ANGjd_report_id`" in out
    assert "**image.png**" in out
    assert "`image/png`" in out
    assert "8,910 bytes" in out
    assert "attachmentId: `ANGjd_image_id`" in out


def test_missing_subject_renders_no_subject_line():
    message = _plaintext_message(subject="", body="Body")
    out = render_message_markdown(message)

    assert out.startswith("Subject: (no subject)\n")


def test_missing_cc_bcc_are_omitted():
    message = _plaintext_message(cc="", bcc="", body="Body")
    out = render_message_markdown(message)

    assert "Cc:" not in out
    assert "Bcc:" not in out


def test_cc_and_bcc_rendered_when_present():
    message = _plaintext_message(
        cc="carol@example.com",
        bcc="secret@example.com",
        body="Body",
    )
    out = render_message_markdown(message)

    assert "Cc: carol@example.com" in out
    assert "Bcc: secret@example.com" in out


def test_missing_message_id_header_renders_none():
    message = _plaintext_message(message_id_header="", body="Body")
    out = render_message_markdown(message)

    assert "Message-ID: (none)" in out


def test_empty_label_ids_renders_none():
    message = _plaintext_message(label_ids=[], body="Body")
    out = render_message_markdown(message)

    assert "Labels: (none)" in out


def test_labels_joined_with_commas_preserving_order():
    message = _plaintext_message(
        label_ids=["INBOX", "UNREAD", "IMPORTANT"],
        body="Body",
    )
    out = render_message_markdown(message)

    assert "Labels: INBOX, UNREAD, IMPORTANT" in out


def test_empty_body_renders_placeholder():
    message = _plaintext_message(body="")
    # Remove the empty body part's data so decode_message_body returns ("", "").
    message["payload"]["parts"][0]["body"] = {}
    out = render_message_markdown(message)

    assert "## Body\n\n_(empty body)_" in out


def test_headers_with_newlines_are_flattened():
    message = _plaintext_message(
        subject="Line1\r\nLine2",
        sender="Ada\n<ada@example.com>",
        body="Body",
    )
    out = render_message_markdown(message)

    # Subject line must not contain CR/LF breaks (only the newline
    # that terminates the `Subject: ...` line).
    first_line = out.split("\n", 1)[0]
    assert first_line == "Subject: Line1 Line2"
    assert "From: Ada <ada@example.com>" in out


def test_snippet_never_emitted_even_when_present():
    message = _plaintext_message(snippet="Quick preview", body="Body")
    out = render_message_markdown(message)

    assert "Snippet:" not in out
    assert "Quick preview" not in out


def test_html_body_with_no_long_urls_has_no_replaced_count(tmp_path, monkeypatch):
    import chat.storage as storage_mod
    monkeypatch.setattr(storage_mod, "CHATS_DIR", tmp_path)

    html = "<html><body><p>No links here.</p></body></html>"
    message = _html_message(html=html)
    out = render_message_markdown(
        message, replace_urls=True, conversation_id="conv-xyz"
    )

    assert "Replaced URL count" not in out
    assert "No links here." in out
