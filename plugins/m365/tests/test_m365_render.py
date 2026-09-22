"""Unit tests for the Microsoft 365 plugin's message rendering and the
pure tool helpers (address parsing, body building, save-path resolution).
"""

from pathlib import Path

import plugins.m365.render as render_mod
from plugins.m365.render import (
    format_email_address,
    format_recipients,
    render_batch_markdown,
    render_message_markdown,
    simplify_folder,
)
from plugins.m365.tools import (
    _build_body,
    _classify_status,
    _parse_address_list,
    _resolve_save_destination,
)


def _message(**overrides) -> dict:
    message = {
        "id": "MSG1",
        "conversationId": "CONV1",
        "internetMessageId": "<abc@outlook.com>",
        "subject": "Quarterly report",
        "receivedDateTime": "2026-08-27T10:00:00Z",
        "categories": [],
        "isRead": False,
        "from": {"emailAddress": {"name": "Ada", "address": "ada@example.com"}},
        "toRecipients": [
            {"emailAddress": {"address": "grace@example.com"}},
        ],
        "ccRecipients": [],
        "bccRecipients": [],
        "body": {"contentType": "text", "content": "Plain body text."},
        "attachments": [],
    }
    message.update(overrides)
    return message


class TestAddressFormatting:
    def test_name_and_address(self):
        assert format_email_address(
            {"emailAddress": {"name": "Ada", "address": "ada@example.com"}}
        ) == "Ada <ada@example.com>"

    def test_address_only(self):
        assert format_email_address(
            {"emailAddress": {"address": "ada@example.com"}}
        ) == "ada@example.com"

    def test_name_equal_to_address_collapses(self):
        assert format_email_address(
            {"emailAddress": {"name": "a@b.c", "address": "a@b.c"}}
        ) == "a@b.c"

    def test_empty(self):
        assert format_email_address(None) == "(unknown)"
        assert format_recipients(None) == ""

    def test_recipient_list(self):
        recipients = [
            {"emailAddress": {"name": "Ada", "address": "ada@example.com"}},
            {"emailAddress": {"address": "grace@example.com"}},
        ]
        assert format_recipients(recipients) == \
            "Ada <ada@example.com>, grace@example.com"


class TestRenderMessage:
    def test_plain_text_message(self):
        doc = render_message_markdown(_message())
        assert "Subject: Quarterly report" in doc
        assert "From: Ada <ada@example.com>" in doc
        assert "To: grace@example.com" in doc
        assert "Cc:" not in doc
        assert "Message-ID: <abc@outlook.com>" in doc
        assert "Graph Message ID: MSG1" in doc
        assert "Conversation ID: CONV1" in doc
        assert "Categories: (none)" in doc
        assert "Read: no" in doc
        assert "## Body\n\nPlain body text." in doc
        assert "_No attachments._" in doc

    def test_html_body_converted_to_markdown(self):
        doc = render_message_markdown(_message(
            body={"contentType": "html",
                  "content": "<p>Hello <b>world</b></p>"},
        ))
        assert "**world**" in doc
        assert "<p>" not in doc

    def test_include_html_fences_raw_html(self):
        doc = render_message_markdown(_message(
            body={"contentType": "html", "content": "<p>Hi</p>"},
        ), include_html=True)
        assert "## Body (HTML)" in doc
        assert "<p>Hi</p>" in doc

    def test_empty_body(self):
        doc = render_message_markdown(_message(body={}))
        assert "_(empty body)_" in doc

    def test_categories_and_read_flag(self):
        doc = render_message_markdown(_message(
            categories=["Quest archived", "red"], isRead=True,
        ))
        assert "Categories: Quest archived, red" in doc
        assert "Read: yes" in doc

    def test_attachments_section(self):
        doc = render_message_markdown(_message(attachments=[
            {"id": "ATT1", "name": "report.pdf",
             "contentType": "application/pdf", "size": 12345},
            {"id": "ATT2", "name": "logo.png", "contentType": "image/png",
             "size": 10, "isInline": True},
        ]))
        assert "- **report.pdf** -- `application/pdf`, 12,345 bytes, " \
               "attachmentId: `ATT1`" in doc
        assert "inline, attachmentId: `ATT2`" in doc

    def test_url_replacement_caches_mapping(self, monkeypatch):
        cached = {}
        monkeypatch.setattr(
            render_mod, "_cache_url_mapping",
            lambda conv, mid, mapping: cached.update(
                {"conv": conv, "mid": mid, "mapping": mapping}),
        )
        long_url = "https://example.com/" + "x" * 80
        doc = render_message_markdown(
            _message(body={"contentType": "html",
                           "content": f'<a href="{long_url}">link</a>'}),
            conversation_id="conv-1",
        )
        assert "(#1#)" in doc
        assert "Replaced URL count: 1" in doc
        assert cached == {"conv": "conv-1", "mid": "MSG1",
                          "mapping": {1: long_url}}

    def test_no_replacement_without_conversation(self):
        long_url = "https://example.com/" + "x" * 80
        doc = render_message_markdown(
            _message(body={"contentType": "html",
                           "content": f'<a href="{long_url}">link</a>'}),
            conversation_id=None,
        )
        assert long_url in doc
        assert "Replaced URL count" not in doc


class TestBatchRender:
    def test_all_success(self):
        doc = render_batch_markdown([
            {"id": "A", "ok": True, "markdown": "doc-a"},
            {"id": "B", "ok": True, "markdown": "doc-b"},
        ])
        assert doc.startswith("# Outlook messages batch")
        assert "Fetched 2 message(s). 0 error(s)." in doc
        assert "## Batch errors" not in doc
        assert "doc-a\n\n===\n\ndoc-b" in doc

    def test_errors_listed_first(self):
        doc = render_batch_markdown([
            {"id": "A", "ok": False, "error_code": "not_found",
             "error_message": "Message not found"},
            {"id": "B", "ok": True, "markdown": "doc-b"},
        ])
        assert "- **A** -- `not_found`: Message not found" in doc
        assert doc.index("## Batch errors") < doc.index("doc-b")


class TestToolHelpers:
    def test_parse_address_list(self):
        assert _parse_address_list("a@b.c, d@e.f ,") == [
            {"emailAddress": {"address": "a@b.c"}},
            {"emailAddress": {"address": "d@e.f"}},
        ]
        assert _parse_address_list(None) == []

    def test_build_body_prefers_markdown(self):
        body = _build_body("plain", "# Heading")
        assert body["contentType"] == "HTML"
        assert "<h1>" in body["content"]

    def test_build_body_plain_text(self):
        assert _build_body("plain", None) == \
            {"contentType": "Text", "content": "plain"}

    def test_classify_status(self):
        assert _classify_status(404, "")[0] == "not_found"
        assert _classify_status(403, "")[0] == "access_denied"
        assert _classify_status(400, "")[0] == "invalid_id"
        assert _classify_status(429, "")[0] == "throttled"
        assert _classify_status(500, "boom") == ("fetch_error", "boom")

    def test_simplify_folder(self):
        folder = simplify_folder({
            "id": "F1", "displayName": "Inbox", "parentFolderId": "root",
            "childFolderCount": 2, "unreadItemCount": 3, "totalItemCount": 9,
        })
        assert folder == {
            "id": "F1", "name": "Inbox", "parent_folder_id": "root",
            "child_folder_count": 2, "unread_item_count": 3,
            "total_item_count": 9,
        }


class TestSaveDestination:
    def test_default_path(self, tmp_path):
        dest = _resolve_save_destination(tmp_path, None, "report.pdf")
        assert dest == (tmp_path / "outlook-attachments" / "report.pdf").resolve()

    def test_explicit_file_path(self, tmp_path):
        dest = _resolve_save_destination(tmp_path, "docs/x.pdf", "report.pdf")
        assert dest == (tmp_path / "docs" / "x.pdf").resolve()

    def test_trailing_slash_means_directory(self, tmp_path):
        dest = _resolve_save_destination(tmp_path, "docs/", "report.pdf")
        assert dest == (tmp_path / "docs" / "report.pdf").resolve()

    def test_existing_directory_means_directory(self, tmp_path):
        (tmp_path / "docs").mkdir()
        dest = _resolve_save_destination(tmp_path, "docs", "report.pdf")
        assert dest == (tmp_path / "docs" / "report.pdf").resolve()

    def test_rejects_absolute_and_traversal(self, tmp_path):
        assert isinstance(
            _resolve_save_destination(tmp_path, "/etc/passwd", "f"), str)
        assert isinstance(
            _resolve_save_destination(tmp_path, "../out.pdf", "f"), str)
