"""Chunked reading of large API responses saved under the workspace
.responses/ blob dir.
"""

import json

from chat.storage import ChatStorage
from chat.gemini_api.constants import (
    _RESPONSE_BLOB_DIR,
    GET_RESPONSE_CONTENT_MAX_CHUNK,
)


# ---------------------------------------------------------------------------
# Get response content handler (chunked reading of large API responses)
# ---------------------------------------------------------------------------

async def _handle_get_response_content(
    conversation_id: str | None,
    hash_val: str,
    offset: int,
    length: int,
) -> str:
    """Read a chunk of a previously-saved large API response file.

    Args:
        conversation_id: Conversation UUID for locating the response file.
        hash_val: The hash identifier from the authed_get stored response.
        offset: 0-indexed character offset to start reading from.
        length: Number of characters to read (maximum GET_RESPONSE_CONTENT_MAX_CHUNK).

    Returns:
        JSON string with the content chunk and metadata, or an error message.
    """
    import re as _re

    if not conversation_id:
        return json.dumps({"error": "Conversation context is not available."})

    if not hash_val or not isinstance(hash_val, str):
        return json.dumps({"error": "hash is required and must be a non-empty string."})

    if not _re.fullmatch(r"[a-f0-9]+", hash_val):
        return json.dumps({"error": "Invalid hash: must contain only hex characters (a-f, 0-9)."})

    if offset < 0:
        return json.dumps({"error": "offset must be >= 0."})

    if length <= 0:
        return json.dumps({"error": "length must be > 0."})

    # Clamp length to max chunk size
    if length > GET_RESPONSE_CONTENT_MAX_CHUNK:
        length = GET_RESPONSE_CONTENT_MAX_CHUNK

    blob_path = ChatStorage._get_conversation_dir(conversation_id) / _RESPONSE_BLOB_DIR / f"response-{hash_val}.blob"

    if not blob_path.exists():
        return json.dumps({"error": f"Response file not found for hash '{hash_val}'."})

    # NOTE: reads entire file into memory to enable character-based (not byte-based)
    # slicing, which avoids splitting multi-byte UTF-8 characters. If response blobs
    # grow very large, consider a streaming approach with character-boundary detection.
    content = blob_path.read_text(encoding="utf-8")
    total_size = len(content)

    if offset >= total_size:
        return json.dumps({
            "error": "offset beyond end of file",
            "total_size": total_size,
        })

    chunk = content[offset:offset + length]
    actual_length = len(chunk)

    return json.dumps({
        "content": chunk,
        "offset": offset,
        "length": actual_length,
        "total_size": total_size,
        "has_more": (offset + actual_length) < total_size,
    })

