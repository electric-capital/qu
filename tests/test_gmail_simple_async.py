"""Tests for the async Gmail Simple read endpoints in
``api/gmail/simple_endpoints.py``.

These endpoints were migrated from the blocking ``googleapiclient`` service to
non-blocking ``httpx.AsyncClient`` calls via ``make_authenticated_request``.
The tests mock ``make_authenticated_request`` (and the early
``get_valid_service_credentials`` 401 gate) so no real network/auth happens.

Covers:
* Single message: correct URL + ``format=full`` param; markdown response.
* Single message: non-2xx upstream -> HTTPException with the upstream status.
* Batch: empty ids -> 400; >50 ids -> 400 (caps preserved).
* Batch: error classification by status code
  (404->not_found, 403->access_denied, 400->invalid_id, other->fetch_error).
* Batch: request-order preservation in the rendered output.
* Batch: one failing id does not abort the whole batch.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

import api.gmail.simple_endpoints as se


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


_USER = {"email": "test@example.com", "google_services_oauth": {"access_token": "x"}}


def _mock_response(status_code: int, json_body: dict | None = None, text: str = ""):
    """Build a fake httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = text
    resp.json.return_value = json_body or {}
    return resp


def _message(msg_id: str) -> dict:
    """A minimal Gmail full-message dict that render_message_markdown accepts."""
    return {
        "id": msg_id,
        "threadId": f"thread-{msg_id}",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": f"Subject {msg_id}"},
                {"name": "From", "value": "a@b.com"},
            ],
            "body": {"data": ""},
        },
    }


# ---------------------------------------------------------------------------
# Single message
# ---------------------------------------------------------------------------

def test_single_message_url_and_format_param():
    captured = {}

    async def fake_mar(client, user, method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _mock_response(200, _message("abc"))

    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=object())), \
         patch.object(se, "make_authenticated_request", new=fake_mar):
        result = _run(se.get_message_simple("abc", user=_USER))

    assert isinstance(result, PlainTextResponse)
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/users/me/messages/abc")
    assert captured["params"] == {"format": "full"}
    assert b"Subject abc" in result.body


def test_single_message_upstream_error_propagates_status():
    async def fake_mar(client, user, method, url, **kwargs):
        return _mock_response(404, text="not found")

    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=object())), \
         patch.object(se, "make_authenticated_request", new=fake_mar):
        with pytest.raises(HTTPException) as exc:
            _run(se.get_message_simple("missing", user=_USER))

    assert exc.value.status_code == 404


def test_single_message_unauthenticated_401():
    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            _run(se.get_message_simple("abc", user=_USER))
    assert exc.value.status_code == 401
    assert exc.value.detail["error"] == "google_services_auth_required"


# ---------------------------------------------------------------------------
# Batch: caps
# ---------------------------------------------------------------------------

def test_batch_empty_ids_400():
    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=object())):
        with pytest.raises(HTTPException) as exc:
            _run(se.get_messages_batch(" , , ", user=_USER))
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "invalid_request"


def test_batch_too_many_ids_400():
    ids = ",".join(str(i) for i in range(51))
    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=object())):
        with pytest.raises(HTTPException) as exc:
            _run(se.get_messages_batch(ids, user=_USER))
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "too_many_ids"


# ---------------------------------------------------------------------------
# Batch: error classification + order + partial failure
# ---------------------------------------------------------------------------

def test_batch_error_classification_and_order_and_partial_failure():
    # Map id -> upstream status. 'ok1'/'ok2' succeed; others map to error codes.
    status_by_id = {
        "ok1": 200,
        "e404": 404,
        "e403": 403,
        "e400": 400,
        "e500": 500,
        "ok2": 200,
    }

    async def fake_mar(client, user, method, url, **kwargs):
        msg_id = url.rsplit("/", 1)[-1]
        status = status_by_id[msg_id]
        if status == 200:
            return _mock_response(200, _message(msg_id))
        return _mock_response(status, text=f"err {status}")

    order = "ok1,e404,e403,e400,e500,ok2"
    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=object())), \
         patch.object(se, "make_authenticated_request", new=fake_mar):
        result = _run(se.get_messages_batch(order, user=_USER))

    body = result.body.decode()

    # Counts line: 2 successes, 4 errors.
    assert "Fetched 2 message(s). 4 error(s)." in body

    # Error classification by status code.
    assert "**e404** -- `not_found`: Message not found" in body
    assert "**e403** -- `access_denied`: Access denied to this message" in body
    assert "**e400** -- `invalid_id`: Invalid message ID format" in body
    assert "**e500** -- `fetch_error`:" in body

    # Order preserved among errors (e404 before e403 before e400 before e500).
    assert body.index("**e404**") < body.index("**e403**") < body.index("**e400**") < body.index("**e500**")

    # Both successful messages rendered (partial failure did not abort batch).
    assert "Subject ok1" in body
    assert "Subject ok2" in body
    # Order preserved among successes.
    assert body.index("Subject ok1") < body.index("Subject ok2")


def test_batch_transport_error_is_fetch_error():
    import httpx

    async def fake_mar(client, user, method, url, **kwargs):
        raise httpx.RequestError("connection reset")

    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=object())), \
         patch.object(se, "make_authenticated_request", new=fake_mar):
        result = _run(se.get_messages_batch("a,b", user=_USER))

    body = result.body.decode()
    assert "Fetched 0 message(s). 2 error(s)." in body
    assert "`fetch_error`: connection reset" in body


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

def test_list_labels():
    async def fake_mar(client, user, method, url, **kwargs):
        assert url.endswith("/users/me/labels")
        return _mock_response(200, {"labels": [{"id": "L1", "name": "Inbox", "type": "system"}]})

    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=object())), \
         patch.object(se, "make_authenticated_request", new=fake_mar):
        result = _run(se.list_labels_simple(user=_USER))

    assert result == {"labels": [{
        "id": "L1", "name": "Inbox", "type": "system",
        "messageListVisibility": None, "labelListVisibility": None,
    }]}


def test_get_single_label():
    async def fake_mar(client, user, method, url, **kwargs):
        assert url.endswith("/users/me/labels/L9")
        return _mock_response(200, {"id": "L9", "name": "Work", "type": "user"})

    with patch.object(se, "get_valid_service_credentials", new=AsyncMock(return_value=object())), \
         patch.object(se, "make_authenticated_request", new=fake_mar):
        result = _run(se.get_label_simple("L9", user=_USER))

    assert result["id"] == "L9"
    assert result["name"] == "Work"
