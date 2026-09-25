"""Google Drive handlers: download_drive_file, google_export_doc and
google_convert_document.
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
        elif _resolve_doc_import_mime(metadata.get("name") or "", source_mime):
            hint = (
                "This is a regular (non-Google-Docs) document. To convert it with "
                "Google Docs' converter (e.g. Word -> PDF) use google_convert_document: "
                'tool_call(tool_name="google_convert_document", arguments={"file_id": "'
                + document_id + '", "format": "' + format_name + '"}). '
                "To fetch its bytes as-is use download_drive_file."
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



# ---------------------------------------------------------------------------
# Google Docs converter for regular documents (google_convert_document)
# ---------------------------------------------------------------------------

DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"

# Source formats Google Drive can import as a Google Doc (Drive ``about``
# ``importFormats`` entries whose target is the Docs mimeType). Keyed by
# file extension; value is the import MIME type sent in the multipart
# upload so Drive picks the right converter.
GOOGLE_DOC_IMPORT_FORMATS: dict[str, str] = {
    "doc": "application/msword",
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "odt": "application/vnd.oasis.opendocument.text",
    "rtf": "application/rtf",
    "txt": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "md": "text/markdown",
}

_GOOGLE_DOC_IMPORT_MIMES: frozenset[str] = frozenset(GOOGLE_DOC_IMPORT_FORMATS.values())

# Google converts documents of up to 50 MB into Google Docs format.
GOOGLE_DOC_IMPORT_MAX_BYTES = 50 * 1024 * 1024

_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def _resolve_doc_import_mime(name: str, upstream_mime: str | None = None) -> str | None:
    """Pick the Drive import MIME type for a source document.

    The file extension of *name* wins (it is what the user sees and what
    Drive keys its converters on); when the extension is unknown the
    Drive-reported *upstream_mime* is accepted if it is itself one of the
    importable types. Returns None when the document cannot be imported
    as a Google Doc.
    """
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    mime = GOOGLE_DOC_IMPORT_FORMATS.get(ext)
    if mime:
        return mime
    if upstream_mime and upstream_mime in _GOOGLE_DOC_IMPORT_MIMES:
        return upstream_mime
    return None


def _build_multipart_import_body(
    metadata: dict, content: bytes, content_type: str, boundary: str,
) -> bytes:
    """Assemble the ``multipart/related`` body for a Drive ``files.create``
    upload with on-import conversion (JSON metadata part + binary part)."""
    return (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--".encode("utf-8")


async def _import_as_google_doc(
    user: dict, name: str, content: bytes, content_type: str,
) -> dict:
    """Upload *content* to Drive as a NEW Google Doc (Drive converts on
    import). Returns ``{"id": ..., "name": ...}`` on success or
    ``{"error": ...}``. The file is created under ``drive.file``, so the
    app can export and delete it afterwards."""
    import uuid

    import httpx
    from fastapi import HTTPException

    from auth.google_credentials import make_authenticated_request
    from chat.action_request_types._drive import (
        _drive_reauth_message,
        _extract_google_api_error,
    )

    boundary = f"quest_convert_{uuid.uuid4().hex}"
    body = _build_multipart_import_body(
        {"name": name, "mimeType": _GOOGLE_DOC_MIME}, content, content_type, boundary,
    )
    url = f"{DRIVE_UPLOAD_BASE}/files?uploadType=multipart&fields=id,name"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await make_authenticated_request(
                client, user, "POST", url,
                content=body,
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            )
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("error"):
            return {"error": detail["error"], "message": detail.get("message", "")}
        return {"error": str(detail)}
    except Exception as exc:  # network / timeout
        return {"error": f"Drive import request failed: {exc}"}

    if response.status_code in (401, 403):
        text = _extract_google_api_error(response).lower()
        if "insufficient" in text or "permission" in text or "scope" in text:
            return {"error": _drive_reauth_message()}
    if response.status_code >= 400:
        return {"error": f"Google Drive API error: {_extract_google_api_error(response)}"}
    try:
        payload = response.json()
    except Exception:
        return {"error": "Unexpected non-JSON response from Drive import."}
    if not isinstance(payload, dict) or not payload.get("id"):
        return {"error": f"Drive import returned no file id: {payload}"}
    return {"id": payload["id"], "name": payload.get("name", name)}


async def _delete_drive_file(user: dict, file_id: str) -> bool:
    """Permanently delete an app-created Drive file. Best-effort: returns
    True when Drive confirmed the delete (or the file is already gone)."""
    import httpx

    from auth.google_credentials import make_authenticated_request

    url = f"{_DRIVE_API_BASE}/files/{urllib.parse.quote(file_id, safe='')}?supportsAllDrives=true"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await make_authenticated_request(client, user, "DELETE", url)
    except Exception:
        return False
    return response.status_code in (200, 204, 404)


async def _handle_google_convert_document(
    user: dict,
    conversation_id: str,
    format: str,
    path: str | None = None,
    file_id: str | None = None,
    filename: str | None = None,
    project_id: str | None = None,
) -> str:
    """Convert a Word / ODT / RTF / HTML / text / Markdown document with
    Google Docs' own converter and save the result in the workspace.

    The sandbox has no LibreOffice or Word, so code-based conversions of
    ``.docx`` files (python-docx + a PDF library) lose layout, fonts,
    images, headers/footers and tables. This tool gets the same output as
    File > Download in Google Docs: the source (a workspace file via
    *path* or a Drive file via *file_id*) is imported as a TEMPORARY
    Google Doc through the Drive multipart ``files.create`` upload with
    ``mimeType = application/vnd.google-apps.document`` (Drive converts on
    import), exported through ``files.export`` in the requested format,
    written to the workspace, and the temporary Doc is deleted again. On
    success nothing remains in the user's Drive; if the delete fails the
    receipt names the leftover Doc so the user can remove it.

    Args:
        user: Authenticated user dict (for Google credentials).
        conversation_id: Conversation UUID.
        format: Target format -- any ``GOOGLE_DOC_EXPORT_FORMATS`` name
            (``pdf``, ``docx``, ``odt``, ``rtf``, ``txt``, ``md``, ``html``,
            ``epub``, ``zip``) or the exact export MIME type.
        path: Workspace-relative path of the source document. Exactly one
            of *path* / *file_id* must be given.
        file_id: Google Drive file id of the source document (a regular
            file such as an uploaded ``.docx``; native Google Docs go
            through ``google_export_doc`` instead).
        filename: Optional workspace filename for the result. Default is
            the source name with the target extension.
        project_id: Optional project UUID for workspace resolution.

    Returns:
        JSON string: success receipt or an ``error`` object.
    """
    from chat.gemini_api.authed_get import _make_authed_request

    resolved = _resolve_doc_export_format(format)
    if resolved is None:
        return json.dumps({
            "error": (
                f"Unsupported target format {format!r}. Supported formats: "
                + ", ".join(sorted(GOOGLE_DOC_EXPORT_FORMATS))
                + "."
            ),
            "supported_formats": {
                name: mime for name, (mime, _ext) in GOOGLE_DOC_EXPORT_FORMATS.items()
            },
        })
    format_name, export_mime, ext = resolved

    path = (path or "").strip()
    file_id = (file_id or "").strip()
    if bool(path) == bool(file_id):
        return json.dumps({
            "error": (
                "Pass exactly one source: 'path' (a workspace file) or "
                "'file_id' (a Google Drive file)."
            )
        })

    # The temporary Doc is created under drive.file; refuse early with a
    # reconnect hint when a stored connection predates that scope.
    google_oauth = user.get("google_services_oauth") or {}
    scopes = {s for s in google_oauth.get("scopes", []) if isinstance(s, str)}
    if scopes and _DRIVE_FILE_SCOPE not in scopes:
        return json.dumps({
            "error": (
                "Converting through Google Docs needs Drive write access for the "
                "temporary Google Doc; reconnect Google Services via Settings > "
                "Data Connections."
            )
        })

    workspace_dir = await _get_workspace_dir(conversation_id, project_id=project_id)

    # --- Step 1: Source bytes --------------------------------------------
    source: dict
    if file_id:
        metadata_url = (
            f"{_DRIVE_API_BASE}/files/{urllib.parse.quote(file_id, safe='')}"
            "?fields=name,mimeType,size&supportsAllDrives=true"
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
            return json.dumps({"error": f"Failed to get file metadata: {metadata.get('error')}"})

        source_name = metadata.get("name") or f"drive_file_{file_id}"
        source_mime = metadata.get("mimeType", "") or ""
        if source_mime == _GOOGLE_DOC_MIME:
            return json.dumps({
                "error": (
                    f"'{source_name}' is already a native Google Doc; export it "
                    "directly with google_export_doc: "
                    'tool_call(tool_name="google_export_doc", arguments={"document_id": "'
                    + file_id + '", "format": "' + format_name + '"}).'
                )
            })
        if source_mime.startswith("application/vnd.google-apps."):
            return json.dumps({
                "error": (
                    f"'{source_name}' is a Google {source_mime.rsplit('.', 1)[-1]} "
                    "file; this tool converts regular documents (Word, ODT, RTF, "
                    "HTML, text, Markdown) through Google Docs and does not "
                    "support Sheets / Slides / other Workspace types."
                )
            })
        import_mime = _resolve_doc_import_mime(source_name, source_mime)
        if import_mime is None:
            return json.dumps({
                "error": (
                    f"'{source_name}' (mimeType {source_mime or 'unknown'}) is not a "
                    "document Google Docs can import. Supported sources: "
                    + ", ".join(f".{e}" for e in GOOGLE_DOC_IMPORT_FORMATS)
                    + ". To fetch the file as-is use download_drive_file."
                )
            })
        try:
            declared_size = int(metadata.get("size") or 0)
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > GOOGLE_DOC_IMPORT_MAX_BYTES:
            return json.dumps({
                "error": (
                    f"'{source_name}' is {declared_size} bytes; Google Docs converts "
                    f"documents of up to {GOOGLE_DOC_IMPORT_MAX_BYTES} bytes (50 MB)."
                )
            })

        download_url = (
            f"{_DRIVE_API_BASE}/files/{urllib.parse.quote(file_id, safe='')}"
            "?alt=media&supportsAllDrives=true"
        )
        download_result = await _make_authed_request(
            download_url, user=user, raw_response=True,
        )
        if isinstance(download_result, str):
            try:
                err = json.loads(download_result)
            except (json.JSONDecodeError, TypeError):
                err = download_result
            return json.dumps({"error": f"Failed to download source file: {err}"})
        content = download_result.content
        source = {"file_id": file_id, "name": source_name}
    else:
        source_path = (workspace_dir / path).resolve()
        try:
            source_path.relative_to(workspace_dir.resolve())
        except ValueError:
            return json.dumps({"error": "Invalid path: results in path outside workspace"})
        if not source_path.is_file():
            return json.dumps({
                "error": f"Workspace file not found: {path}",
                "hint": "Use list_workspace_files to see the available files.",
            })
        source_name = source_path.name
        import_mime = _resolve_doc_import_mime(source_name)
        if import_mime is None:
            return json.dumps({
                "error": (
                    f"'{source_name}' is not a document Google Docs can import. "
                    "Supported sources: "
                    + ", ".join(f".{e}" for e in GOOGLE_DOC_IMPORT_FORMATS)
                    + "."
                )
            })
        try:
            content = source_path.read_bytes()
        except OSError as e:
            return json.dumps({"error": f"Failed to read workspace file: {e}"})
        source = {"path": path, "name": source_name}

    if len(content) > GOOGLE_DOC_IMPORT_MAX_BYTES:
        return json.dumps({
            "error": (
                f"'{source_name}' is {len(content)} bytes; Google Docs converts "
                f"documents of up to {GOOGLE_DOC_IMPORT_MAX_BYTES} bytes (50 MB)."
            )
        })
    if not content:
        return json.dumps({"error": f"'{source_name}' is empty; nothing to convert."})

    source_stem = source_name.rsplit(".", 1)[0] if "." in source_name else source_name

    # --- Step 2: Import as a temporary Google Doc -------------------------
    imported = await _import_as_google_doc(
        user, f"[Quest temp] {source_stem}", content, import_mime,
    )
    if "error" in imported:
        return json.dumps({
            "error": f"Failed to import '{source_name}' into Google Docs: {imported['error']}",
            **({"message": imported["message"]} if imported.get("message") else {}),
        })
    temp_doc_id = imported["id"]

    # --- Step 3: Export in the requested format, then delete the temp Doc -
    try:
        export_url = (
            f"{_DRIVE_API_BASE}/files/{urllib.parse.quote(temp_doc_id, safe='')}/export"
            f"?mimeType={urllib.parse.quote(export_mime, safe='')}"
        )
        export_result = await _make_authed_request(
            export_url, user=user, raw_response=True,
        )
    finally:
        temp_deleted = await _delete_drive_file(user, temp_doc_id)

    temp_doc_info: dict = {"id": temp_doc_id, "deleted": temp_deleted}
    leftover_warning = None
    if not temp_deleted:
        leftover_warning = (
            f"The temporary Google Doc '[Quest temp] {source_stem}' "
            f"(https://docs.google.com/document/d/{temp_doc_id}/edit) could not be "
            "deleted automatically; tell the user so they can remove it from Drive."
        )

    if isinstance(export_result, str):
        try:
            err = json.loads(export_result)
        except (json.JSONDecodeError, TypeError):
            err = export_result
        return json.dumps({
            "error": f"Failed to export the converted document as {format_name}: {err}",
            "hint": (
                "Google caps exports at 10 MB of exported content; very large "
                "documents may need a lighter format such as txt or md."
            ),
            "temporary_google_doc": temp_doc_info,
            **({"warning": leftover_warning} if leftover_warning else {}),
        })

    file_bytes = export_result.content
    content_type = export_result.headers.get("content-type", export_mime)

    # --- Step 4: Save to workspace ---------------------------------------
    if filename:
        clean_filename = _sanitize_workspace_filename(filename)
    else:
        clean_filename = _sanitize_workspace_filename(source_stem)
        if clean_filename:
            clean_filename += ext
    if not clean_filename:
        clean_filename = f"converted_{temp_doc_id}{ext}"

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
    receipt = {
        "status": "success",
        "filename": clean_filename,
        "size_bytes": len(file_bytes),
        "format": format_name,
        "export_mime_type": export_mime,
        "content_type": content_type,
        "source": {**source, "import_mime_type": import_mime},
        "temporary_google_doc": temp_doc_info,
        "message": (
            f"'{source_name}' converted with Google Docs to {format_name} and saved as "
            f"'{clean_filename}' ({len(file_bytes)} bytes) in the workspace. "
            + ("The temporary Google Doc was deleted; nothing was left in Drive. "
               if temp_deleted else "")
            + "The user can download it from the file browser; to inspect it yourself "
            f'use tool_call(tool_name="get_workspace_file", arguments={{"path": "{clean_filename}"}}).'
        ),
    }
    if leftover_warning:
        receipt["warning"] = leftover_warning
    return json.dumps(receipt)
