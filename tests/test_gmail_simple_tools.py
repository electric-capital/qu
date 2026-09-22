"""Tests for the Gmail Simple dynamic tool handlers in
``chat/gemini_api/tool_handlers/gmail_simple.py``.

The handlers wrap the ``api/gmail`` endpoint functions (which stay registered
as HTTP routes for sandboxed scripts). These tests mock the endpoint functions
themselves, so they cover the tool-side plumbing:

* Result conversion: PlainTextResponse -> text, dict -> JSON,
  HTTPException (dict and string detail) -> ``{"error": ...}`` JSON.
* get_gmail_messages: single id -> single-message endpoint, several ids ->
  batch endpoint with comma-joined ids; argument validation and bool coercion.
* list_gmail_labels: with and without label_id.
* get_gmail_message_urls: with and without identifiers.
* create_gmail_draft: conversation_id injection, unknown/missing params.
* send_gmail_to_self: request model construction and validation, plus
  conversation_id injection and attachment threading.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

from chat.gemini_api.tool_handlers import (
    _handle_create_gmail_draft,
    _handle_get_gmail_message_urls,
    _handle_get_gmail_messages,
    _handle_list_gmail_labels,
    _handle_send_gmail_to_self,
    _run_gmail_simple_endpoint,
)


def _run(coro):
    return asyncio.run(coro)


_USER = {"email": "test@example.com", "google_services_oauth": {"access_token": "x"}}
_CONV = "conv-123"


# ---------------------------------------------------------------------------
# _run_gmail_simple_endpoint result/error conversion
# ---------------------------------------------------------------------------

class TestRunGmailSimpleEndpoint:
    def test_plain_text_response_returns_body(self):
        async def endpoint():
            return PlainTextResponse("Subject: hi\n\n## Body\n\nhello")

        result = _run(_run_gmail_simple_endpoint(endpoint()))
        assert result == "Subject: hi\n\n## Body\n\nhello"

    def test_dict_result_returns_json(self):
        async def endpoint():
            return {"labels": [{"id": "INBOX"}]}

        result = _run(_run_gmail_simple_endpoint(endpoint()))
        assert json.loads(result) == {"labels": [{"id": "INBOX"}]}

    def test_http_exception_dict_detail_preserved(self):
        async def endpoint():
            raise HTTPException(status_code=401, detail={
                "error": "google_services_auth_required",
                "message": "Connect Google Services.",
            })

        result = json.loads(_run(_run_gmail_simple_endpoint(endpoint())))
        assert result["error"] == "google_services_auth_required"
        assert result["message"] == "Connect Google Services."

    def test_http_exception_string_detail_wrapped(self):
        async def endpoint():
            raise HTTPException(status_code=404, detail="Message not found")

        result = json.loads(_run(_run_gmail_simple_endpoint(endpoint())))
        assert result["error"] == "gmail_http_404"
        assert result["message"] == "Message not found"

    def test_unexpected_exception_wrapped(self):
        async def endpoint():
            raise RuntimeError("boom")

        result = json.loads(_run(_run_gmail_simple_endpoint(endpoint())))
        assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# get_gmail_messages
# ---------------------------------------------------------------------------

class TestGetGmailMessages:
    def test_single_id_uses_single_message_endpoint(self):
        single = AsyncMock(return_value=PlainTextResponse("single doc"))
        batch = AsyncMock(return_value=PlainTextResponse("batch doc"))
        with patch("api.gmail.get_message_simple", single), \
             patch("api.gmail.get_messages_batch", batch):
            result = _run(_handle_get_gmail_messages(_USER, _CONV, ["msg-1"]))

        assert result == "single doc"
        batch.assert_not_called()
        args, kwargs = single.call_args
        assert args[0] == "msg-1"
        assert kwargs["user"] is _USER
        assert kwargs["conversation_id"] == _CONV
        assert kwargs["include_html"] is False
        assert kwargs["include_urls"] is False

    def test_multiple_ids_use_batch_endpoint_with_joined_ids(self):
        single = AsyncMock(return_value=PlainTextResponse("single doc"))
        batch = AsyncMock(return_value=PlainTextResponse("batch doc"))
        with patch("api.gmail.get_message_simple", single), \
             patch("api.gmail.get_messages_batch", batch):
            result = _run(
                _handle_get_gmail_messages(_USER, _CONV, ["a", " b ", "c"])
            )

        assert result == "batch doc"
        single.assert_not_called()
        args, kwargs = batch.call_args
        assert args[0] == "a,b,c"
        assert kwargs["conversation_id"] == _CONV

    def test_string_bool_flags_are_coerced(self):
        single = AsyncMock(return_value=PlainTextResponse("doc"))
        with patch("api.gmail.get_message_simple", single):
            _run(_handle_get_gmail_messages(
                _USER, _CONV, ["msg-1"],
                include_html="true", include_urls="false",
            ))

        kwargs = single.call_args.kwargs
        assert kwargs["include_html"] is True
        assert kwargs["include_urls"] is False

    def test_non_list_rejected(self):
        result = json.loads(_run(_handle_get_gmail_messages(_USER, _CONV, "msg-1")))
        assert "must be a list" in result["error"]

    def test_empty_list_rejected(self):
        result = json.loads(_run(_handle_get_gmail_messages(_USER, _CONV, ["  "])))
        assert "at least one" in result["error"]

    def test_endpoint_http_error_becomes_error_json(self):
        single = AsyncMock(side_effect=HTTPException(status_code=404, detail="nope"))
        with patch("api.gmail.get_message_simple", single):
            result = json.loads(_run(_handle_get_gmail_messages(_USER, _CONV, ["x"])))
        assert result["error"] == "gmail_http_404"


# ---------------------------------------------------------------------------
# list_gmail_labels
# ---------------------------------------------------------------------------

class TestListGmailLabels:
    def test_lists_all_labels_by_default(self):
        list_labels = AsyncMock(return_value={"labels": []})
        get_label = AsyncMock(return_value={"id": "INBOX"})
        with patch("api.gmail.list_labels_simple", list_labels), \
             patch("api.gmail.get_label_simple", get_label):
            result = json.loads(_run(_handle_list_gmail_labels(_USER)))

        assert result == {"labels": []}
        get_label.assert_not_called()
        assert list_labels.call_args.kwargs["user"] is _USER

    def test_label_id_fetches_single_label(self):
        list_labels = AsyncMock(return_value={"labels": []})
        get_label = AsyncMock(return_value={"id": "INBOX"})
        with patch("api.gmail.list_labels_simple", list_labels), \
             patch("api.gmail.get_label_simple", get_label):
            result = json.loads(
                _run(_handle_list_gmail_labels(_USER, label_id=" INBOX "))
            )

        assert result == {"id": "INBOX"}
        list_labels.assert_not_called()
        assert get_label.call_args.args[0] == "INBOX"


# ---------------------------------------------------------------------------
# get_gmail_message_urls
# ---------------------------------------------------------------------------

class TestGetGmailMessageUrls:
    def test_identifiers_joined_for_lookup(self):
        by_ids = AsyncMock(return_value={"urls": {"1": "https://a"}})
        all_urls = AsyncMock(return_value={"urls": {}})
        with patch("api.gmail.get_urls_by_identifiers", by_ids), \
             patch("api.gmail.get_urls_for_message", all_urls):
            result = json.loads(_run(_handle_get_gmail_message_urls(
                _USER, _CONV, "msg-1", identifiers=[1, 2, 3],
            )))

        assert result == {"urls": {"1": "https://a"}}
        all_urls.assert_not_called()
        args, kwargs = by_ids.call_args
        assert args[0] == "msg-1"
        assert args[1] == "1,2,3"
        assert kwargs["conversation_id"] == _CONV

    def test_no_identifiers_fetches_all_mappings(self):
        by_ids = AsyncMock(return_value={})
        all_urls = AsyncMock(return_value={"urls": {"1": "https://a"}})
        with patch("api.gmail.get_urls_by_identifiers", by_ids), \
             patch("api.gmail.get_urls_for_message", all_urls):
            result = json.loads(
                _run(_handle_get_gmail_message_urls(_USER, _CONV, "msg-1"))
            )

        assert result == {"urls": {"1": "https://a"}}
        by_ids.assert_not_called()

    def test_missing_message_id_rejected(self):
        result = json.loads(_run(_handle_get_gmail_message_urls(_USER, _CONV, "  ")))
        assert "message_id is required" in result["error"]

    def test_non_list_identifiers_rejected(self):
        result = json.loads(_run(_handle_get_gmail_message_urls(
            _USER, _CONV, "msg-1", identifiers="1,2",
        )))
        assert "must be a list" in result["error"]


# ---------------------------------------------------------------------------
# create_gmail_draft
# ---------------------------------------------------------------------------

class TestCreateGmailDraft:
    def test_builds_request_with_injected_conversation_id(self):
        create = AsyncMock(return_value={"id": "draft-1", "message": {"id": "m1"}})
        with patch("api.gmail.create_draft", create):
            result = json.loads(_run(_handle_create_gmail_draft(
                _USER, _CONV,
                {"to": "a@example.com", "subject": "Hi", "body": "text"},
            )))

        assert result["id"] == "draft-1"
        draft_request = create.call_args.args[0]
        assert draft_request.to == "a@example.com"
        assert draft_request.conversation_id == _CONV
        assert create.call_args.kwargs["user"] is _USER

    def test_model_supplied_conversation_id_is_ignored(self):
        create = AsyncMock(return_value={"id": "draft-1"})
        with patch("api.gmail.create_draft", create):
            _run(_handle_create_gmail_draft(
                _USER, _CONV,
                {"to": "a@example.com", "subject": "Hi", "body": "t",
                 "conversation_id": "spoofed"},
            ))

        assert create.call_args.args[0].conversation_id == _CONV

    def test_attachments_are_validated(self):
        create = AsyncMock(return_value={"id": "draft-1"})
        with patch("api.gmail.create_draft", create):
            _run(_handle_create_gmail_draft(
                _USER, _CONV,
                {"to": "a@example.com", "subject": "Hi", "body": "t",
                 "attachments": [{"type": "workspace", "workspace_path": "out.csv"}]},
            ))

        attachment = create.call_args.args[0].attachments[0]
        assert attachment.type == "workspace"
        assert attachment.workspace_path == "out.csv"

    def test_unknown_parameter_rejected(self):
        result = json.loads(_run(_handle_create_gmail_draft(
            _USER, _CONV,
            {"to": "a@example.com", "subject": "Hi", "body": "t", "bodyy": "typo"},
        )))
        assert "Unknown draft parameter" in result["error"]
        assert "bodyy" in result["error"]

    def test_missing_required_fields_rejected(self):
        result = json.loads(_run(_handle_create_gmail_draft(
            _USER, _CONV, {"body": "no recipient"},
        )))
        assert "Invalid draft parameters" in result["error"]


# ---------------------------------------------------------------------------
# send_gmail_to_self
# ---------------------------------------------------------------------------

class TestSendGmailToSelf:
    def test_builds_request_model(self):
        send = AsyncMock(return_value={"status": "sent"})
        with patch("api.gmail.send_email_to_self", send):
            result = json.loads(_run(_handle_send_gmail_to_self(
                _USER, "Daily Summary", "# Summary",
            )))

        assert result == {"status": "sent"}
        email_request = send.call_args.args[0]
        assert email_request.subject == "Daily Summary"
        assert email_request.body_md == "# Summary"
        assert send.call_args.kwargs["user"] is _USER

    def test_missing_body_md_rejected(self):
        result = json.loads(_run(_handle_send_gmail_to_self(_USER, "Subject", None)))
        assert "Invalid parameters" in result["error"]

    def test_attachments_and_conversation_id_threaded(self):
        send = AsyncMock(return_value={"success": True})
        with patch("api.gmail.send_email_to_self", send):
            _run(_handle_send_gmail_to_self(
                _USER, "Chart", "![t](cid:chart.png)",
                conversation_id=_CONV,
                attachments=[{"type": "workspace", "workspace_path": "out/chart.png"}],
            ))

        email_request = send.call_args.args[0]
        assert email_request.conversation_id == _CONV
        assert email_request.attachments[0].type == "workspace"
        assert email_request.attachments[0].workspace_path == "out/chart.png"

    def test_invalid_attachment_shape_rejected(self):
        result = json.loads(_run(_handle_send_gmail_to_self(
            _USER, "Chart", "body",
            conversation_id=_CONV,
            attachments=[{"type": "ftp", "workspace_path": "x"}],
        )))
        assert "Invalid parameters" in result["error"]

    def test_dispatch_passes_context_conversation_id(self):
        from chat.gemini_api.tool_dispatch import _tool_send_gmail_to_self

        send = AsyncMock(return_value={"success": True})
        ctx = SimpleNamespace(user=_USER, conversation_id=_CONV)
        with patch("api.gmail.send_email_to_self", send):
            _run(_tool_send_gmail_to_self(ctx, {
                "subject": "S", "body_md": "b",
                "conversation_id": "spoofed",
                "attachments": [{"type": "workspace", "workspace_path": "a.csv"}],
            }))

        email_request = send.call_args.args[0]
        assert email_request.conversation_id == _CONV
        assert email_request.attachments[0].workspace_path == "a.csv"


# ---------------------------------------------------------------------------
# Registry / dispatch wiring
# ---------------------------------------------------------------------------

class TestRegistryWiring:
    def test_tools_registered(self):
        from chat.llm.tool_schemas import TOOL_CALL_REGISTRY

        for name in (
            "get_gmail_messages",
            "list_gmail_labels",
            "get_gmail_message_urls",
            "create_gmail_draft",
            "send_gmail_to_self",
        ):
            assert name in TOOL_CALL_REGISTRY
            assert TOOL_CALL_REGISTRY[name]["name"] == name
