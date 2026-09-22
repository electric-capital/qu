"""Tests for the server-side collapsed-card fields on action requests.

Every handler exposes ``resolved_label`` (the status word shown after an
approved execution, e.g. "Created") and ``summary_snippet(params)`` (a
short params summary). These replaced the frontend's hardcoded
per-request-type chain, so the mappings are pinned here.
"""

import asyncio

import pytest

from chat.action_request_types import get_handler
from chat.action_request_types.registry import (
    get_preview_for_request,
    get_summary_snippet,
)


def _label(type_name: str) -> str:
    return get_handler(type_name).resolved_label


def _snippet(type_name: str, params: dict) -> str:
    return get_handler(type_name).summary_snippet(params)


# ---------------------------------------------------------------------------
# resolved_label
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_name,label", [
    ("create_memory", "Sent"),
    ("create_calendar_invite", "Created"),
    ("edit_calendar_event", "Updated"),
    ("create_drive_folder", "Created"),
    ("create_skill", "Created"),
    ("edit_skill", "Saved"),
    ("edit_google_spreadsheet", "Saved"),
    ("upload_to_drive", "Uploaded"),
    ("run_user_subagent", "Launched"),
    ("subagent_return", "Returned"),
])
def test_resolved_labels(type_name, label):
    assert _label(type_name) == label


def test_plugin_send_resolved_labels(slack_plugin, twitter_plugin, telegram_plugin):
    assert _label("send_slack_dm") == "Sent"
    assert _label("send_slack_message") == "Sent"
    assert _label("send_twitter_dm") == "Sent"
    assert _label("send_telegram_message") == "Sent"


# ---------------------------------------------------------------------------
# summary_snippet
# ---------------------------------------------------------------------------

def test_message_snippets(slack_plugin, twitter_plugin):
    assert _snippet("send_slack_dm", {"message": "hi there"}) == "hi there"
    assert _snippet("send_twitter_dm", {"message": "yo"}) == "yo"
    assert _snippet("create_calendar_invite", {"summary": "Standup"}) == "Standup"
    assert _snippet("edit_calendar_event", {"event_id": "abc", "summary": "New"}) == "New"
    assert _snippet("edit_calendar_event", {
        "event_id": "abc", "summary": "New", "current_event": {"summary": "Old"},
    }) == "Old"


def test_drive_snippets_cover_multi_and_legacy_shapes():
    assert _snippet("upload_to_drive",
                    {"files": [{"path": "out/report.pdf"}]}) == "report.pdf"
    assert _snippet("upload_to_drive", {
        "files": [{"path": "a.txt"}, {"path": "b.txt"}],
    }) == "2 files: a.txt, ..."
    # Legacy single {path, filename?} shape.
    assert _snippet("upload_to_drive", {"path": "x/y.csv"}) == "y.csv"


def test_spreadsheet_and_skill_snippets():
    assert _snippet("edit_google_spreadsheet", {
        "spreadsheet_title": "Budget", "tab": "Q3", "range": "A1:B2",
    }) == "Budget -- Q3!A1:B2"
    assert _snippet("create_skill", {"name": "deploy", "target": "project"}) == "Project: deploy"
    assert _snippet("edit_skill", {
        "skill_id": "abcd1234-rest", "project_autoload": True,
    }) == "Skill #abcd1234 -- auto-load on"


def test_subagent_snippets():
    assert _snippet("run_user_subagent", {
        "target_user_email": "a@b.co", "prompt": "do it",
    }) == "Subagent for a@b.co: do it"
    assert _snippet("subagent_return", {
        "files": [{"path": "f"}], "caller_email": "a@b.co",
    }) == "Returned 1 file(s) to a@b.co"
    assert _snippet("subagent_return", {"response": "all done"}) == "all done"


# ---------------------------------------------------------------------------
# Registry-level behavior
# ---------------------------------------------------------------------------

def test_get_summary_snippet_truncates_to_80_chars(telegram_plugin):
    snippet = get_summary_snippet("send_telegram_message", {"message": "x" * 200})
    assert snippet == "x" * 80 + "..."


def test_get_summary_snippet_unknown_type_is_empty():
    assert get_summary_snippet("no_such_type", {"message": "hi"}) == ""


def test_preview_payload_carries_collapsed_card_fields(slack_plugin):
    payload = asyncio.run(get_preview_for_request(
        "send_slack_dm", {"user_id": "U1", "message": "hello"},
    ))
    assert payload["resolved_label"] == "Sent"
    assert payload["summary_snippet"] == "hello"
    # Unknown types fall back to the generic label with no snippet.
    fallback = asyncio.run(get_preview_for_request("mystery_type", {}))
    assert fallback["resolved_label"] == "Sent"
    assert fallback["summary_snippet"] == ""
