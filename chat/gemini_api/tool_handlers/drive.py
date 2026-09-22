"""Google Drive handlers: download_drive_file and google_export_doc.
"""

import json
import urllib.parse

from chat.gemini_api.tool_handlers._common import (
    _get_workspace_dir,
    _publish_file_list_changed,
    _sanitize_workspace_filename,
)


# ---------------------------------------------------------------------------
# Drive file download handler
# ---------------------------------------------------------------------------

async def _handle_download_drive_file(
    user: dict,
    conversation_id: str,
    file_id: str,
    filename: str | None = None,
    project_id: str | None = None,
) -> str:
    """Download a Google Drive file to the conversation workspace.

    Fetches file metadata (to determine the filename) and then downloads
    the binary content using alt=media.  The file is saved to the
    workspace so the LLM can read it with get_workspace_file.

    Args:
        user: Authenticated user dict (for Google credentials).
        conversation_id: Conversation UUID.
        file_id: Google Drive file ID.
        filename: Optional filename override.  When None, the original
            filename from Drive metadata is used.
        project_id: Optional project UUID for workspace resolution.

    Returns:
        JSON string with the result (success with filename and path,
        or error details).
    """
    from chat.gemini_api.authed_get import _make_authed_request

    DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"

    # --- Step 1: Get file metadata to determine filename if not provided ---
    if not filename:
        metadata_url = (
            f"{DRIVE_API_BASE}/files/{file_id}"
            "?fields=name,mimeType&supportsAllDrives=true"
        )
        metadata_result = await _make_authed_request(
            metadata_url, user=user, raw_response=False,
        )
        # metadata_result is a JSON string on success or error
        try:
            metadata = json.loads(metadata_result)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": f"Failed to parse Drive metadata: {metadata_result}"})

        if "error" in metadata:
            return json.dumps({"error": f"Failed to get file metadata: {metadata.get('error')}"})

        filename = metadata.get("name", f"drive_file_{file_id}")

    # --- Step 2: Download binary content with alt=media ---
    download_url = (
        f"{DRIVE_API_BASE}/files/{file_id}"
        "?alt=media&supportsAllDrives=true"
    )
    download_result = await _make_authed_request(
        download_url, user=user, raw_response=True,
    )

    # If we got a string back, it's an error
    if isinstance(download_result, str):
        try:
            err = json.loads(download_result)
        except (json.JSONDecodeError, TypeError):
            err = download_result
        return json.dumps({"error": f"Failed to download file: {err}"})

    # download_result is an httpx.Response with binary content
    file_bytes = download_result.content
    content_type = download_result.headers.get("content-type", "application/octet-stream")

    # --- Step 3: Save to workspace ---
    workspace_dir = await _get_workspace_dir(conversation_id, project_id=project_id)

    # Sanitize filename: strip path separators to prevent traversal
    clean_filename = filename.replace("/", "_").replace("\\", "_").strip()
    if not clean_filename:
        clean_filename = f"drive_file_{file_id}"

    file_path = (workspace_dir / clean_filename).resolve()

    # Ensure resolved path is within workspace
    try:
        file_path.relative_to(workspace_dir.resolve())
    except ValueError:
        return json.dumps({"error": "Invalid filename: results in path outside workspace"})

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(file_bytes)
    except Exception as e:
        return json.dumps({"error": f"Failed to save file to workspace: {e}"})

    _publish_file_list_changed(user["id"], conversation_id, project_id)
    return json.dumps({
        "status": "success",
        "filename": clean_filename,
        "size_bytes": len(file_bytes),
        "content_type": content_type,
        "message": (
            f"File '{clean_filename}' ({len(file_bytes)} bytes) has been saved to the workspace. "
            f"Use get_workspace_file to read or analyze it: "
            f'tool_call(tool_name="get_workspace_file", arguments={{"path": "{clean_filename}"}})'
        ),
    })


# ---------------------------------------------------------------------------
# Google Doc export handler
# ---------------------------------------------------------------------------

_GOOGLE_DOC_MIME = "application/vnd.google-apps.document"

# Every export format Google Drive supports for native Google Docs
# (Drive API "Export MIME types for Google Workspace documents").
# Keyed by the short format name the model passes; value is
# (export MIME type, file extension).
GOOGLE_DOC_EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "pdf": ("application/pdf", ".pdf"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "odt": ("application/vnd.oasis.opendocument.text", ".odt"),
    "rtf": ("application/rtf", ".rtf"),
    "txt": ("text/plain", ".txt"),
    "md": ("text/markdown", ".md"),
    "html": ("text/html", ".html"),
    "epub": ("application/epub+zip", ".epub"),
    # Zipped HTML (the web-page export bundle with images).
    "zip": ("application/zip", ".zip"),
}

# Reverse map so the model may also pass the exact export MIME type.
_GOOGLE_DOC_EXPORT_BY_MIME: dict[str, str] = {
    mime: name for name, (mime, _ext) in GOOGLE_DOC_EXPORT_FORMATS.items()
}


def _resolve_doc_export_format(fmt: str | None) -> tuple[str, str, str] | None:
    """Resolve a model-supplied format to ``(name, mime, ext)``.

    Accepts the short name (``"pdf"``), the same with a leading dot or in
    any case (``".PDF"``), or the exact export MIME type. Returns None when
    the value is not a supported Google Docs export format.
    """
    if not fmt or not isinstance(fmt, str):
        return None
    key = fmt.strip()
    if key in _GOOGLE_DOC_EXPORT_BY_MIME:
        key = _GOOGLE_DOC_EXPORT_BY_MIME[key]
    key = key.lower().lstrip(".")
    if key == "markdown":
        key = "md"
    elif key in ("text", "plain"):
        key = "txt"
    elif key == "htm":
        key = "html"
    entry = GOOGLE_DOC_EXPORT_FORMATS.get(key)
    if entry is None:
        return None
    return key, entry[0], entry[1]


async def _handle_google_export_doc(
    user: dict,
    conversation_id: str,
    document_id: str,
    format: str,
    filename: str | None = None,
    project_id: str | None = None,
) -> str:
    """Export a native Google Doc to the workspace in a chosen format.

    Google Docs have no downloadable bytes of their own (``alt=media`` on
    ``files/{id}`` fails for Google Workspace files), so the content has to
    be converted through the Drive ``files.export`` endpoint. This handler
    validates the requested format against ``GOOGLE_DOC_EXPORT_FORMATS``,
    fetches the file's metadata to confirm it really is a Google Doc (and
    to derive the default filename), runs the export, and writes the bytes
    to the conversation / project workspace. Read the result back with
    ``get_workspace_file``.

    Args:
        user: Authenticated user dict (for Google credentials).
        conversation_id: Conversation UUID.
        document_id: Google Doc id (the ``/document/d/<id>/`` URL segment).
        format: Short export format name (``pdf``, ``docx``, ``odt``,
            ``rtf``, ``txt``, ``md``, ``html``, ``epub``, ``zip``) or the
            exact export MIME type.
        filename: Optional workspace filename override. When None, the
            Doc's title plus the format's extension is used.
        project_id: Optional project UUID for workspace resolution.

    Returns:
        JSON string: success receipt (filename, size, format, mime) or an
        ``error`` object.
    """
    from chat.gemini_api.authed_get import _make_authed_request

    DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"

    document_id = (document_id or "").strip()
    if not document_id:
        return json.dumps({"error": "document_id is required."})

    resolved = _resolve_doc_export_format(format)
    if resolved is None:
        return json.dumps({
            "error": (
                f"Unsupported export format {format!r}. Supported formats: "
                + ", ".join(sorted(GOOGLE_DOC_EXPORT_FORMATS))
                + "."
            ),
            "supported_formats": {
                name: mime for name, (mime, _ext) in GOOGLE_DOC_EXPORT_FORMATS.items()
            },
        })
    format_name, export_mime, ext = resolved

    # --- Step 1: Metadata -- confirm it's a Google Doc, get the title ------
    metadata_url = (
        f"{DRIVE_API_BASE}/files/{document_id}"
        "?fields=name,mimeType&supportsAllDrives=true"
    )
    metadata_result = await _make_authed_request(
        metadata_url, user=user, raw_response=False,
    )
    try:
        metadata = json.loads(metadata_result)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": f"Failed to parse Drive metadata: {metadata_result}"})
    if not isinstance(metadata, dict):
        return json.dumps({"error": f"Unexpected Drive metadata response: {metadata_result}"})
    if "error" in metadata:
        return json.dumps({"error": f"Failed to get document metadata: {metadata.get('error')}"})

    source_mime = metadata.get("mimeType", "")
    if source_mime != _GOOGLE_DOC_MIME:
        if source_mime.startswith("application/vnd.google-apps."):
            hint = (
                "This tool only exports native Google Docs. Google Sheets / "
                "Slides / other Workspace files are not supported by it."
            )
        else:
            hint = (
                "This is a regular (non-Google-Docs) file with its own bytes; "
                "use download_drive_file instead: "
                'tool_call(tool_name="download_drive_file", arguments={"file_id": "'
                + document_id + '"}).'
            )
        return json.dumps({
            "error": (
                f"File '{metadata.get('name', document_id)}' is not a Google Doc "
                f"(mimeType {source_mime or 'unknown'}). {hint}"
            )
        })

    doc_title = metadata.get("name") or f"google_doc_{document_id}"

    # --- Step 2: Export via files.export ---------------------------------
    export_url = (
        f"{DRIVE_API_BASE}/files/{document_id}/export"
        f"?mimeType={urllib.parse.quote(export_mime, safe='')}"
    )
    export_result = await _make_authed_request(
        export_url, user=user, raw_response=True,
    )
    if isinstance(export_result, str):
        try:
            err = json.loads(export_result)
        except (json.JSONDecodeError, TypeError):
            err = export_result
        return json.dumps({
            "error": f"Failed to export document as {format_name}: {err}",
            "hint": (
                "Google caps exports at 10 MB of exported content; very large "
                "documents may need a lighter format such as txt or md."
            ),
        })

    file_bytes = export_result.content
    content_type = export_result.headers.get("content-type", export_mime)

    # --- Step 3: Save to workspace ---------------------------------------
    workspace_dir = await _get_workspace_dir(conversation_id, project_id=project_id)

    if filename:
        clean_filename = _sanitize_workspace_filename(filename)
    else:
        clean_filename = _sanitize_workspace_filename(doc_title)
        if clean_filename:
            clean_filename += ext
    if not clean_filename:
        clean_filename = f"google_doc_{document_id}{ext}"

    file_path = (workspace_dir / clean_filename).resolve()
    try:
        file_path.relative_to(workspace_dir.resolve())
    except ValueError:
        return json.dumps({"error": "Invalid filename: results in path outside workspace"})

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(file_bytes)
    except Exception as e:
        return json.dumps({"error": f"Failed to save file to workspace: {e}"})

    _publish_file_list_changed(user["id"], conversation_id, project_id)
    return json.dumps({
        "status": "success",
        "filename": clean_filename,
        "size_bytes": len(file_bytes),
        "format": format_name,
        "export_mime_type": export_mime,
        "content_type": content_type,
        "document_title": doc_title,
        "message": (
            f"Google Doc '{doc_title}' exported as {format_name} to "
            f"'{clean_filename}' ({len(file_bytes)} bytes) in the workspace. "
            f"Use get_workspace_file to read or analyze it: "
            f'tool_call(tool_name="get_workspace_file", arguments={{"path": "{clean_filename}"}})'
        ),
    })

