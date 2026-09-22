"""Regression tests for security finding #279215.

The Gmail batch proxy validated a different multipart structure from the one
it forwarded upstream: it split the body on a fixed ``--batch_*`` pattern
while Gmail splits on the boundary declared in the Content-Type header. A
caller-chosen boundary that does not start with ``batch_`` collapsed the
whole body into one validator segment, so only its first request line (an
allowed GET) was checked and a later ``messages.send`` POST rode through with
the user's credential. The validator now splits on the declared boundary.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from api.gmail.helpers import parse_and_validate_batch


def _batch(boundary: str, *request_blocks: str) -> str:
    body = ""
    for block in request_blocks:
        body += f"--{boundary}\r\nContent-Type: application/http\r\n\r\n{block}\r\n"
    return body + f"--{boundary}--\r\n"


ALLOWED_GET = "GET /gmail/v1/users/me/messages/readable HTTP/1.1\r\n"
HIDDEN_SEND = (
    "POST /gmail/v1/users/me/messages/send HTTP/1.1\r\n"
    "Content-Type: application/json\r\n\r\n"
    + json.dumps({"raw": "aGk"})
)


def _detail(exc_info) -> dict:
    return exc_info.value.detail


def test_hidden_send_behind_custom_boundary_is_rejected():
    boundary = "quest_boundary_not_seen_by_validator"
    body = _batch(boundary, ALLOWED_GET, HIDDEN_SEND)
    with pytest.raises(HTTPException) as exc_info:
        parse_and_validate_batch(body, f"multipart/mixed; boundary={boundary}")
    assert exc_info.value.status_code == 400
    assert _detail(exc_info)["error"] == "unsupported_method"


def test_hidden_send_behind_quoted_boundary_is_rejected():
    boundary = "b=1"
    body = _batch(boundary, ALLOWED_GET, HIDDEN_SEND)
    with pytest.raises(HTTPException) as exc_info:
        parse_and_validate_batch(body, f'multipart/mixed; boundary="{boundary}"')
    assert _detail(exc_info)["error"] == "unsupported_method"


def test_documented_batch_boundary_shape_still_accepted():
    # Mirrors the shape the model is taught in api/gmail/instructions.py
    # (LF line endings, no HTTP version suffix).
    body = (
        "--batch_boundary\nContent-Type: application/http\nContent-ID: <item1>\n\n"
        "GET /gmail/v1/users/me/messages/MESSAGE_ID_1\n\n"
        "--batch_boundary\nContent-Type: application/http\nContent-ID: <item2>\n\n"
        "GET /gmail/v1/users/me/messages/MESSAGE_ID_2\n\n"
        "--batch_boundary--"
    )
    parse_and_validate_batch(body, "multipart/mixed; boundary=batch_boundary")


def test_forbidden_path_still_rejected_with_custom_boundary():
    body = _batch("xyz", "GET /gmail/v1/users/me/settings/forwardingAddresses HTTP/1.1\r\n")
    with pytest.raises(HTTPException) as exc_info:
        parse_and_validate_batch(body, "multipart/mixed; boundary=xyz")
    assert exc_info.value.status_code == 403
    assert _detail(exc_info)["error"] == "forbidden_path"


@pytest.mark.parametrize(
    "content_type",
    ["multipart/mixed", "application/json", "multipart/form-data; boundary=abc", ""],
)
def test_content_type_without_mixed_boundary_is_rejected(content_type):
    body = _batch("abc", ALLOWED_GET)
    with pytest.raises(HTTPException) as exc_info:
        parse_and_validate_batch(body, content_type)
    assert exc_info.value.status_code == 400
    assert _detail(exc_info)["error"] == "invalid_batch_format"


def test_empty_batch_under_declared_boundary_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        parse_and_validate_batch("--abc--\r\n", "multipart/mixed; boundary=abc")
    assert _detail(exc_info)["error"] == "empty_batch"
