"""Tests for large response protection in authed_get.

Covers the size gate, file-based storage of large responses, and the
get_response_content chunked reading tool.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from chat.gemini_api.authed_get import handle_authed_get
from chat.gemini_api.constants import (
    AUTHED_GET_SIZE_LIMIT,
    GET_RESPONSE_CONTENT_MAX_CHUNK,
    _RESPONSE_BLOB_DIR,
)
from chat.gemini_api.tool_handlers import _handle_get_response_content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(size: int) -> str:
    """Create a string payload of exactly `size` bytes when UTF-8 encoded."""
    return "x" * size


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests: Size gate in handle_authed_get
# ---------------------------------------------------------------------------

class TestSizeGate:
    """Tests for the authed_get response size gate."""

    def test_small_response_passes_through(self, tmp_path):
        """Responses under the size limit are returned unchanged."""
        small_response = '{"result": "ok"}'
        assert len(small_response.encode("utf-8")) <= AUTHED_GET_SIZE_LIMIT

        with patch(
            "chat.storage.ChatStorage._get_conversation_dir",
            return_value=tmp_path,
        ), patch(
            "chat.gemini_api.authed_get._make_authed_request",
            new_callable=AsyncMock,
            return_value=small_response,
        ):
            result = _run(handle_authed_get(
                "https://pro-api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
                user={"id": 1},
                conversation_id="test-conv-123",
            ))

        assert result == small_response

    def test_large_response_rejected_without_force(self, tmp_path):
        """Responses over the size limit return error when force_large_response is False."""
        large_response = _make_response(AUTHED_GET_SIZE_LIMIT + 1000)

        with patch(
            "chat.storage.ChatStorage._get_conversation_dir",
            return_value=tmp_path,
        ), patch(
            "chat.gemini_api.authed_get._make_authed_request",
            new_callable=AsyncMock,
            return_value=large_response,
        ):
            result = _run(handle_authed_get(
                "https://sheets.googleapis.com/v4/spreadsheets/abc/values/Sheet1",
                user={"id": 1},
                conversation_id="test-conv-123",
            ))

        parsed = json.loads(result)
        assert parsed["error"] == "response_too_large"
        assert parsed["response_size_bytes"] == len(large_response.encode("utf-8"))
        assert parsed["size_limit_bytes"] == AUTHED_GET_SIZE_LIMIT
        assert "force_large_response" in parsed["message"]

    def test_large_response_stored_with_force(self, tmp_path):
        """Responses over the limit with force_large_response=True are saved to file."""
        large_response = _make_response(AUTHED_GET_SIZE_LIMIT + 2000)

        with patch(
            "chat.storage.ChatStorage._get_conversation_dir",
            return_value=tmp_path,
        ), patch(
            "chat.gemini_api.authed_get._make_authed_request",
            new_callable=AsyncMock,
            return_value=large_response,
        ):
            result = _run(handle_authed_get(
                "https://sheets.googleapis.com/v4/spreadsheets/abc/values/Sheet1",
                user={"id": 1},
                force_large_response=True,
                conversation_id="test-conv-123",
            ))

        parsed = json.loads(result)
        assert parsed["stored"] is True
        assert "hash" in parsed
        assert parsed["size_bytes"] == len(large_response.encode("utf-8"))

        # Verify file was written
        blob_path = tmp_path / _RESPONSE_BLOB_DIR / f"response-{parsed['hash']}.blob"
        assert blob_path.exists()
        assert blob_path.read_text(encoding="utf-8") == large_response

    def test_error_response_passes_through_regardless_of_size(self, tmp_path):
        """Error responses (with 'error' key) bypass the size gate."""
        error_body = {"error": {"status_code": 500, "details": "x" * (AUTHED_GET_SIZE_LIMIT + 5000)}}
        error_response = json.dumps(error_body)
        assert len(error_response.encode("utf-8")) > AUTHED_GET_SIZE_LIMIT

        with patch(
            "chat.storage.ChatStorage._get_conversation_dir",
            return_value=tmp_path,
        ), patch(
            "chat.gemini_api.authed_get._make_authed_request",
            new_callable=AsyncMock,
            return_value=error_response,
        ):
            result = _run(handle_authed_get(
                "https://sheets.googleapis.com/v4/spreadsheets/abc/values/Sheet1",
                user={"id": 1},
                conversation_id="test-conv-123",
            ))

        # The error response should be returned unchanged
        assert result == error_response

    def test_force_large_without_conversation_id(self, tmp_path):
        """force_large_response=True without conversation_id returns an error."""
        large_response = _make_response(AUTHED_GET_SIZE_LIMIT + 1000)

        with patch(
            "chat.storage.ChatStorage._get_conversation_dir",
            return_value=tmp_path,
        ), patch(
            "chat.gemini_api.authed_get._make_authed_request",
            new_callable=AsyncMock,
            return_value=large_response,
        ):
            result = _run(handle_authed_get(
                "https://sheets.googleapis.com/v4/spreadsheets/abc/values/Sheet1",
                user={"id": 1},
                force_large_response=True,
                conversation_id=None,
            ))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "conversation" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# Tests: get_response_content handler
# ---------------------------------------------------------------------------

class TestGetResponseContent:
    """Tests for the get_response_content chunked reading handler."""

    def _write_blob(self, conv_dir: Path, hash_val: str, content: str) -> Path:
        """Write a response blob file and return its path."""
        blob_dir = conv_dir / _RESPONSE_BLOB_DIR
        blob_dir.mkdir(parents=True, exist_ok=True)
        blob_path = blob_dir / f"response-{hash_val}.blob"
        blob_path.write_text(content, encoding="utf-8")
        return blob_path

    def test_read_chunk_at_offset_zero(self, tmp_path):
        """Reading at offset 0 returns the beginning of the file."""
        content = "A" * 10000
        self._write_blob(tmp_path, "abcdef1234567890", content)

        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "abcdef1234567890", offset=0, length=100,
            ))
        parsed = json.loads(result)
        assert parsed["content"] == "A" * 100
        assert parsed["offset"] == 0
        assert parsed["length"] == 100
        assert parsed["total_size"] == 10000
        assert parsed["has_more"] is True

    def test_read_chunk_at_middle_offset(self, tmp_path):
        """Reading at a middle offset returns the correct slice."""
        content = "A" * 100 + "B" * 100 + "C" * 100
        self._write_blob(tmp_path, "abcdef1234567890", content)

        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "abcdef1234567890", offset=100, length=100,
            ))
        parsed = json.loads(result)
        assert parsed["content"] == "B" * 100
        assert parsed["offset"] == 100
        assert parsed["length"] == 100
        assert parsed["total_size"] == 300

    def test_offset_beyond_end_of_file(self, tmp_path):
        """Offset at or beyond end of file returns an error."""
        content = "A" * 100
        self._write_blob(tmp_path, "abcdef1234567890", content)

        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "abcdef1234567890", offset=100, length=50,
            ))
        parsed = json.loads(result)
        assert "error" in parsed
        assert parsed["total_size"] == 100

    def test_clamps_length_at_end_of_file(self, tmp_path):
        """When near end of file, actual length returned is less than requested."""
        content = "A" * 100
        self._write_blob(tmp_path, "abcdef1234567890", content)

        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "abcdef1234567890", offset=80, length=50,
            ))
        parsed = json.loads(result)
        assert parsed["content"] == "A" * 20
        assert parsed["length"] == 20
        assert parsed["has_more"] is False

    def test_rejects_invalid_hash(self, tmp_path):
        """Non-hex hash characters are rejected (path traversal prevention)."""
        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "../etc/passwd", offset=0, length=100,
            ))
        parsed = json.loads(result)
        assert "error" in parsed
        assert "hex" in parsed["error"].lower()

    def test_nonexistent_file(self, tmp_path):
        """Returns error for a hash that has no corresponding file."""
        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "deadbeef12345678", offset=0, length=100,
            ))
        parsed = json.loads(result)
        assert "error" in parsed
        assert "deadbeef12345678" in parsed["error"]

    def test_clamps_length_to_max_chunk(self, tmp_path):
        """Requests for more than GET_RESPONSE_CONTENT_MAX_CHUNK are clamped."""
        content = "A" * (GET_RESPONSE_CONTENT_MAX_CHUNK + 5000)
        self._write_blob(tmp_path, "abcdef1234567890", content)

        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "abcdef1234567890",
                offset=0, length=GET_RESPONSE_CONTENT_MAX_CHUNK + 5000,
            ))
        parsed = json.loads(result)
        assert parsed["length"] == GET_RESPONSE_CONTENT_MAX_CHUNK
        assert parsed["has_more"] is True

    def test_no_conversation_id(self, tmp_path):
        """Returns error when conversation_id is None."""
        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                None, "abcdef1234567890", offset=0, length=100,
            ))
        parsed = json.loads(result)
        assert "error" in parsed

    def test_negative_offset(self, tmp_path):
        """Returns error for negative offset."""
        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "abcdef1234567890", offset=-1, length=100,
            ))
        parsed = json.loads(result)
        assert "error" in parsed

    def test_zero_length(self, tmp_path):
        """Returns error for zero length."""
        with patch("chat.storage.ChatStorage._get_conversation_dir", return_value=tmp_path):
            result = _run(_handle_get_response_content(
                "test-conv-123", "abcdef1234567890", offset=0, length=0,
            ))
        parsed = json.loads(result)
        assert "error" in parsed
