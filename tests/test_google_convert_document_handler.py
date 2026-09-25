"""Tests for the ``google_convert_document`` tool
(``_handle_google_convert_document``).

Regular documents (a ``.docx`` on Drive or in the workspace, ``.odt``,
``.rtf``, ``.html``, ``.txt``, ``.md``) are converted with Google Docs' own
converter: import as a temporary Google Doc (multipart ``files.create``
with the Docs mimeType), ``files.export`` in the target format, delete the
temporary Doc, write the bytes to the workspace. Covered here:

* Import-format resolution (extension wins, Drive mimeType fallback) and
  the multipart body builder.
* Source selection: exactly one of ``path`` / ``file_id``; workspace
  containment; missing / unsupported / empty / oversized sources reject
  before any upstream call.
* Happy paths for both sources: upstream call sequence, temp-Doc naming,
  export URL, delete, bytes on disk, receipt shape, file-list publish.
* Drive sources that are native Docs (pointer to ``google_export_doc``),
  other Workspace types, or non-importable files.
* Failure ordering: import failure makes no export/delete; export failure
  still deletes the temp Doc; delete failure surfaces a warning but keeps
  the converted file.
* ``google_export_doc`` now points convertible non-Doc sources here.
* Registry / dispatch wiring, allow-list exclusions, prompt + skill text.

All HTTP calls are mocked -- no real network traffic.
"""

import asyncio
import contextlib
import json
import urllib.parse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chat.gemini_api.tool_handlers import (
    GOOGLE_DOC_EXPORT_FORMATS,
    GOOGLE_DOC_IMPORT_FORMATS,
    _handle_google_convert_document,
    _handle_google_export_doc,
    _resolve_doc_import_mime,
)
from chat.gemini_api.tool_handlers.drive import (
    GOOGLE_DOC_IMPORT_MAX_BYTES,
    _build_multipart_import_body,
)


def _run(coro):
    return asyncio.run(coro)


DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
USER = {
    "id": 7,
    "email": "u@example.com",
    "google_services_oauth": {"scopes": ["https://www.googleapis.com/auth/drive.readonly", DRIVE_FILE_SCOPE]},
}
FILE_ID = "1DrIvE_fIlE-Id"
TEMP_ID = "1TeMpDoC_iD"
DOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _response(content: bytes, content_type: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    resp.headers = {"content-type": content_type}
    return resp


@contextlib.contextmanager
def _patch_workspace(tmp_path: Path):
    async def fake_workspace(*args, **kwargs):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    with patch("chat.gemini_api.tool_handlers.drive._get_workspace_dir", new=fake_workspace), \
         patch("chat.gemini_api.tool_handlers._get_workspace_dir", new=fake_workspace):
        yield


def _fake_authed_requests(metadata, download, export):
    """AsyncMock for ``_make_authed_request``: metadata (JSON string),
    alt=media download (response or error string), export (response or
    error string). Records every URL + kwargs."""
    calls: list[tuple[str, dict]] = []

    async def side_effect(url, *args, **kwargs):
        calls.append((url, kwargs))
        parsed = urllib.parse.urlparse(url)
        if parsed.path.endswith("/export"):
            return export
        if "alt=media" in parsed.query:
            return download
        return metadata

    mock = AsyncMock(side_effect=side_effect)
    mock.calls = calls
    return mock


def _convert(tmp_path, *, fmt="pdf", path=None, file_id=None, filename=None,
             user=USER, metadata=None, download=None, export=None,
             imported=None, deleted=True, project_id=None, workspace_files=None):
    metadata = metadata if metadata is not None else json.dumps(
        {"name": "Q3 Report.docx", "mimeType": DOCX_MIME, "size": "1234"}
    )
    download = download if download is not None else _response(b"PK\x03\x04docx", DOCX_MIME)
    export = export if export is not None else _response(b"%PDF-1.7 converted", "application/pdf")
    imported = imported if imported is not None else {"id": TEMP_ID, "name": "[Quest temp] Q3 Report"}

    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    for name, data in (workspace_files or {}).items():
        target = ws / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    req = _fake_authed_requests(metadata, download, export)
    import_mock = AsyncMock(return_value=imported)
    delete_mock = AsyncMock(return_value=deleted)
    publish = MagicMock()
    with patch("chat.gemini_api.authed_get._make_authed_request", req), \
         patch("chat.gemini_api.tool_handlers.drive._import_as_google_doc", import_mock), \
         patch("chat.gemini_api.tool_handlers.drive._delete_drive_file", delete_mock), \
         _patch_workspace(tmp_path), \
         patch("chat.gemini_api.tool_handlers.drive._publish_file_list_changed", publish):
        result = json.loads(_run(_handle_google_convert_document(
            user, "conv-1", fmt, path=path, file_id=file_id, filename=filename,
            project_id=project_id,
        )))
    return result, {"req": req, "import": import_mock, "delete": delete_mock,
                    "publish": publish, "ws": ws}


def _written(ws: Path) -> set[str]:
    return {p.name for p in ws.iterdir()}


# ---------------------------------------------------------------------------
# Import-format resolution + multipart body
# ---------------------------------------------------------------------------


class TestImportResolution:
    def test_every_extension_maps_to_its_mime(self):
        for ext, mime in GOOGLE_DOC_IMPORT_FORMATS.items():
            assert _resolve_doc_import_mime(f"file.{ext}") == mime
            assert _resolve_doc_import_mime(f"FILE.{ext.upper()}") == mime

    def test_extension_wins_over_upstream_mime(self):
        assert _resolve_doc_import_mime("notes.md", "application/octet-stream") == "text/markdown"

    def test_upstream_mime_fallback_for_unknown_extension(self):
        assert _resolve_doc_import_mime("report", DOCX_MIME) == DOCX_MIME
        assert _resolve_doc_import_mime("report.bin", DOCX_MIME) == DOCX_MIME

    def test_unsupported(self):
        assert _resolve_doc_import_mime("scan.pdf", "application/pdf") is None
        assert _resolve_doc_import_mime("data.xlsx") is None
        assert _resolve_doc_import_mime("") is None

    def test_import_set_pinned(self):
        assert set(GOOGLE_DOC_IMPORT_FORMATS) == {
            "doc", "docx", "odt", "rtf", "txt", "html", "htm", "md",
        }

    def test_multipart_body_layout(self):
        body = _build_multipart_import_body(
            {"name": "x", "mimeType": DOC_MIME}, b"\x00binary\xff", DOCX_MIME, "BOUND",
        )
        assert body.startswith(b"--BOUND\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n")
        assert json.dumps({"name": "x", "mimeType": DOC_MIME}).encode() in body
        assert f"\r\n--BOUND\r\nContent-Type: {DOCX_MIME}\r\n\r\n".encode() + b"\x00binary\xff" in body
        assert body.endswith(b"\r\n--BOUND--")


# ---------------------------------------------------------------------------
# Pre-flight rejections (no upstream call)
# ---------------------------------------------------------------------------


class TestPreflight:
    def test_unknown_format(self, tmp_path):
        result, m = _convert(tmp_path, fmt="xlsx", path="a.docx",
                             workspace_files={"a.docx": b"x"})
        assert "Unsupported target format" in result["error"]
        assert set(result["supported_formats"]) == set(GOOGLE_DOC_EXPORT_FORMATS)
        assert m["req"].calls == [] and m["import"].await_count == 0

    def test_both_sources_rejected(self, tmp_path):
        result, m = _convert(tmp_path, path="a.docx", file_id=FILE_ID)
        assert "exactly one source" in result["error"]
        assert m["req"].calls == [] and m["import"].await_count == 0

    def test_no_source_rejected(self, tmp_path):
        result, m = _convert(tmp_path)
        assert "exactly one source" in result["error"]
        assert m["import"].await_count == 0

    def test_missing_drive_file_scope(self, tmp_path):
        user = {**USER, "google_services_oauth": {"scopes": ["https://www.googleapis.com/auth/drive.readonly"]}}
        result, m = _convert(tmp_path, path="a.docx", user=user,
                             workspace_files={"a.docx": b"x"})
        assert "reconnect Google Services" in result["error"]
        assert m["req"].calls == [] and m["import"].await_count == 0

    def test_no_stored_scopes_defers_to_upstream_auth_error(self, tmp_path):
        # A user without any Google connection isn't pre-rejected on scope;
        # the first upstream call returns the standard auth error instead.
        user = {"id": 7, "email": "u@example.com"}
        result, m = _convert(
            tmp_path, file_id=FILE_ID, user=user,
            metadata=json.dumps({"error": "google_services_auth_required", "message": "..."}),
        )
        assert "google_services_auth_required" in result["error"]
        assert m["import"].await_count == 0

    def test_workspace_file_missing(self, tmp_path):
        result, m = _convert(tmp_path, path="nope.docx")
        assert "not found" in result["error"]
        assert "list_workspace_files" in result["hint"]
        assert m["import"].await_count == 0

    def test_workspace_path_traversal(self, tmp_path):
        (tmp_path / "secret.docx").write_bytes(b"x")
        result, m = _convert(tmp_path, path="../secret.docx")
        assert "outside workspace" in result["error"]
        assert m["import"].await_count == 0

    def test_workspace_unsupported_extension(self, tmp_path):
        result, m = _convert(tmp_path, path="scan.pdf", workspace_files={"scan.pdf": b"%PDF"})
        assert "not a document Google Docs can import" in result["error"]
        assert ".docx" in result["error"]
        assert m["import"].await_count == 0

    def test_workspace_empty_file(self, tmp_path):
        result, m = _convert(tmp_path, path="empty.docx", workspace_files={"empty.docx": b""})
        assert "empty" in result["error"]
        assert m["import"].await_count == 0

    def test_workspace_oversized(self, tmp_path):
        big = tmp_path / "workspace" / "big.txt"
        big.parent.mkdir(parents=True, exist_ok=True)
        with big.open("wb") as fh:
            fh.truncate(GOOGLE_DOC_IMPORT_MAX_BYTES + 1)
        result, m = _convert(tmp_path, path="big.txt")
        assert "50 MB" in result["error"]
        assert m["import"].await_count == 0

    def test_drive_declared_size_over_cap_skips_download(self, tmp_path):
        result, m = _convert(
            tmp_path, file_id=FILE_ID,
            metadata=json.dumps({"name": "huge.docx", "mimeType": DOCX_MIME,
                                 "size": str(GOOGLE_DOC_IMPORT_MAX_BYTES + 1)}),
        )
        assert "50 MB" in result["error"]
        assert len(m["req"].calls) == 1  # metadata only
        assert m["import"].await_count == 0


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestWorkspaceSource:
    def test_docx_to_pdf(self, tmp_path):
        result, m = _convert(tmp_path, path="Q3 Report.docx",
                             workspace_files={"Q3 Report.docx": b"PK\x03\x04docx"})

        assert result["status"] == "success"
        assert result["filename"] == "Q3 Report.pdf"
        assert result["format"] == "pdf"
        assert result["export_mime_type"] == "application/pdf"
        assert result["source"] == {"path": "Q3 Report.docx", "name": "Q3 Report.docx",
                                    "import_mime_type": DOCX_MIME}
        assert result["temporary_google_doc"] == {"id": TEMP_ID, "deleted": True}
        assert "warning" not in result
        assert "nothing was left in Drive" in result["message"]
        assert (m["ws"] / "Q3 Report.pdf").read_bytes() == b"%PDF-1.7 converted"
        m["publish"].assert_called_once_with(USER["id"], "conv-1", None)

        # Import got the workspace bytes, a "[Quest temp]" name and the docx MIME.
        m["import"].assert_awaited_once_with(
            USER, "[Quest temp] Q3 Report", b"PK\x03\x04docx", DOCX_MIME,
        )
        # Only the export went through _make_authed_request (no Drive metadata
        # or download for a workspace source), against the TEMP doc.
        assert len(m["req"].calls) == 1
        export_url, export_kwargs = m["req"].calls[0]
        parsed = urllib.parse.urlparse(export_url)
        assert parsed.path == f"/drive/v3/files/{TEMP_ID}/export"
        assert urllib.parse.parse_qs(parsed.query) == {"mimeType": ["application/pdf"]}
        assert export_kwargs["raw_response"] is True
        m["delete"].assert_awaited_once_with(USER, TEMP_ID)

    def test_markdown_to_docx_with_filename_override(self, tmp_path):
        result, m = _convert(
            tmp_path, fmt="docx", path="notes/summary.md", filename="Summary.docx",
            workspace_files={"notes/summary.md": b"# Hello"},
            export=_response(b"PK\x03\x04out", DOCX_MIME),
        )
        assert result["filename"] == "Summary.docx"
        assert (m["ws"] / "Summary.docx").read_bytes() == b"PK\x03\x04out"
        m["import"].assert_awaited_once_with(USER, "[Quest temp] summary", b"# Hello", "text/markdown")
        assert "mimeType=application%2Fvnd.openxmlformats-officedocument.wordprocessingml.document" in m["req"].calls[0][0]

    def test_filename_override_sanitized(self, tmp_path):
        result, m = _convert(
            tmp_path, fmt="txt", path="a.docx", filename="../.hidden/out.txt",
            workspace_files={"a.docx": b"x"}, export=_response(b"t", "text/plain"),
        )
        assert result["status"] == "success"
        assert "/" not in result["filename"] and not result["filename"].startswith(".")
        assert (m["ws"] / result["filename"]).exists()
        assert not (tmp_path / ".hidden").exists()

    def test_project_id_forwarded_to_publish(self, tmp_path):
        _result, m = _convert(tmp_path, path="a.docx", project_id="proj-9",
                              workspace_files={"a.docx": b"x"})
        m["publish"].assert_called_once_with(USER["id"], "conv-1", "proj-9")


class TestDriveSource:
    def test_drive_docx_to_pdf(self, tmp_path):
        result, m = _convert(tmp_path, file_id=FILE_ID)

        assert result["status"] == "success"
        assert result["filename"] == "Q3 Report.pdf"
        assert result["source"] == {"file_id": FILE_ID, "name": "Q3 Report.docx",
                                    "import_mime_type": DOCX_MIME}
        assert (m["ws"] / "Q3 Report.pdf").read_bytes() == b"%PDF-1.7 converted"
        # No copy of the source docx is left in the workspace.
        assert _written(m["ws"]) == {"Q3 Report.pdf"}

        # metadata -> alt=media download -> export (of the temp doc).
        assert len(m["req"].calls) == 3
        meta_url, meta_kwargs = m["req"].calls[0]
        assert meta_url.startswith(f"https://www.googleapis.com/drive/v3/files/{FILE_ID}?")
        assert "fields=name,mimeType,size" in meta_url and meta_kwargs["raw_response"] is False
        dl_url, dl_kwargs = m["req"].calls[1]
        assert urllib.parse.urlparse(dl_url).path == f"/drive/v3/files/{FILE_ID}"
        assert "alt=media" in dl_url and dl_kwargs["raw_response"] is True
        assert urllib.parse.urlparse(m["req"].calls[2][0]).path == f"/drive/v3/files/{TEMP_ID}/export"
        m["import"].assert_awaited_once_with(USER, "[Quest temp] Q3 Report", b"PK\x03\x04docx", DOCX_MIME)
        m["delete"].assert_awaited_once_with(USER, TEMP_ID)

    def test_drive_mime_fallback_when_name_has_no_extension(self, tmp_path):
        result, m = _convert(
            tmp_path, file_id=FILE_ID,
            metadata=json.dumps({"name": "Proposal", "mimeType": "application/rtf"}),
            download=_response(b"{\\rtf1}", "application/rtf"),
        )
        assert result["status"] == "success"
        assert result["filename"] == "Proposal.pdf"
        assert result["source"]["import_mime_type"] == "application/rtf"

    def test_native_google_doc_points_to_export_tool(self, tmp_path):
        result, m = _convert(
            tmp_path, file_id=FILE_ID,
            metadata=json.dumps({"name": "Spec", "mimeType": DOC_MIME}),
        )
        assert "already a native Google Doc" in result["error"]
        assert "google_export_doc" in result["error"] and FILE_ID in result["error"]
        assert len(m["req"].calls) == 1 and m["import"].await_count == 0

    def test_sheet_not_supported(self, tmp_path):
        result, m = _convert(
            tmp_path, file_id=FILE_ID,
            metadata=json.dumps({"name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet"}),
        )
        assert "does not support Sheets" in result["error"]
        assert m["import"].await_count == 0

    def test_non_importable_drive_file_points_to_download(self, tmp_path):
        result, m = _convert(
            tmp_path, file_id=FILE_ID,
            metadata=json.dumps({"name": "scan.pdf", "mimeType": "application/pdf"}),
        )
        assert "not a document Google Docs can import" in result["error"]
        assert "download_drive_file" in result["error"]
        assert len(m["req"].calls) == 1 and m["import"].await_count == 0

    def test_metadata_error_short_circuits(self, tmp_path):
        result, m = _convert(tmp_path, file_id=FILE_ID,
                             metadata=json.dumps({"error": "HTTP 404: File not found"}))
        assert "Failed to get file metadata" in result["error"]
        assert m["import"].await_count == 0
        assert not _written(m["ws"])

    def test_download_error_short_circuits(self, tmp_path):
        result, m = _convert(tmp_path, file_id=FILE_ID,
                             download=json.dumps({"error": "HTTP 403: forbidden"}))
        assert "Failed to download source file" in result["error"]
        assert m["import"].await_count == 0
        assert not _written(m["ws"])


# ---------------------------------------------------------------------------
# Failure ordering around the temporary Doc
# ---------------------------------------------------------------------------


class TestTempDocLifecycle:
    def test_import_failure_makes_no_export_or_delete(self, tmp_path):
        result, m = _convert(
            tmp_path, path="a.docx", workspace_files={"a.docx": b"x"},
            imported={"error": "Google Drive API error: The user's Drive storage quota has been exceeded."},
        )
        assert "Failed to import 'a.docx'" in result["error"]
        assert "quota" in result["error"]
        assert m["req"].calls == []
        m["delete"].assert_not_awaited()
        m["publish"].assert_not_called()
        assert _written(m["ws"]) == {"a.docx"}

    def test_export_failure_still_deletes_temp_doc(self, tmp_path):
        result, m = _convert(
            tmp_path, path="a.docx", workspace_files={"a.docx": b"x"},
            export=json.dumps({"error": "HTTP 403: This file is too large to be exported."}),
        )
        assert "Failed to export the converted document as pdf" in result["error"]
        assert "10 MB" in result["hint"]
        assert result["temporary_google_doc"] == {"id": TEMP_ID, "deleted": True}
        assert "warning" not in result
        m["delete"].assert_awaited_once_with(USER, TEMP_ID)
        m["publish"].assert_not_called()
        assert _written(m["ws"]) == {"a.docx"}

    def test_export_exception_still_deletes_temp_doc(self, tmp_path):
        boom = AsyncMock(side_effect=RuntimeError("network down"))
        delete_mock = AsyncMock(return_value=True)
        ws = tmp_path / "workspace"; ws.mkdir(parents=True, exist_ok=True)
        (ws / "a.docx").write_bytes(b"x")
        with patch("chat.gemini_api.authed_get._make_authed_request", boom), \
             patch("chat.gemini_api.tool_handlers.drive._import_as_google_doc",
                   AsyncMock(return_value={"id": TEMP_ID, "name": "t"})), \
             patch("chat.gemini_api.tool_handlers.drive._delete_drive_file", delete_mock), \
             _patch_workspace(tmp_path):
            with pytest.raises(RuntimeError):
                _run(_handle_google_convert_document(USER, "conv-1", "pdf", path="a.docx"))
        delete_mock.assert_awaited_once_with(USER, TEMP_ID)

    def test_delete_failure_keeps_file_and_warns(self, tmp_path):
        result, m = _convert(tmp_path, path="a.docx", workspace_files={"a.docx": b"x"},
                             deleted=False)
        assert result["status"] == "success"
        assert (m["ws"] / "a.pdf").exists()
        assert result["temporary_google_doc"] == {"id": TEMP_ID, "deleted": False}
        assert TEMP_ID in result["warning"] and "could not be deleted" in result["warning"]
        assert "nothing was left in Drive" not in result["message"]


# ---------------------------------------------------------------------------
# Real import / delete helpers (httpx mocked)
# ---------------------------------------------------------------------------


class TestDriveHelpers:
    def _mock_client(self, response):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    def test_import_posts_multipart_with_docs_mimetype(self):
        from chat.gemini_api.tool_handlers.drive import _import_as_google_doc
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"id": TEMP_ID, "name": "[Quest temp] a"}
        make = AsyncMock(return_value=resp)
        with patch("auth.google_credentials.make_authenticated_request", make):
            out = _run(_import_as_google_doc(USER, "[Quest temp] a", b"bytes", DOCX_MIME))
        assert out == {"id": TEMP_ID, "name": "[Quest temp] a"}
        _client, user, method, url = make.await_args.args
        kwargs = make.await_args.kwargs
        assert user is USER and method == "POST"
        assert url == "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name"
        assert kwargs["headers"]["Content-Type"].startswith("multipart/related; boundary=quest_convert_")
        body = kwargs["content"]
        assert json.dumps({"name": "[Quest temp] a", "mimeType": DOC_MIME}).encode() in body
        assert f"Content-Type: {DOCX_MIME}\r\n\r\nbytes".encode() in body

    def test_import_scope_error_maps_to_reauth(self):
        from chat.gemini_api.tool_handlers.drive import _import_as_google_doc
        resp = MagicMock(status_code=403)
        resp.json.return_value = {"error": {"message": "Insufficient Permission"}}
        with patch("auth.google_credentials.make_authenticated_request", AsyncMock(return_value=resp)):
            out = _run(_import_as_google_doc(USER, "n", b"b", DOCX_MIME))
        assert "reconnecting Google Services" in out["error"]

    def test_import_auth_required_passthrough(self):
        from fastapi import HTTPException
        from chat.gemini_api.tool_handlers.drive import _import_as_google_doc
        exc = HTTPException(status_code=401, detail={"error": "google_services_auth_required", "message": "connect"})
        with patch("auth.google_credentials.make_authenticated_request", AsyncMock(side_effect=exc)):
            out = _run(_import_as_google_doc(USER, "n", b"b", DOCX_MIME))
        assert out == {"error": "google_services_auth_required", "message": "connect"}

    def test_delete_sends_delete_and_tolerates_404(self):
        from chat.gemini_api.tool_handlers.drive import _delete_drive_file
        for status, expected in ((204, True), (404, True), (500, False)):
            make = AsyncMock(return_value=MagicMock(status_code=status))
            with patch("auth.google_credentials.make_authenticated_request", make):
                assert _run(_delete_drive_file(USER, TEMP_ID)) is expected
            _client, _user, method, url = make.await_args.args
            assert method == "DELETE"
            assert url == f"https://www.googleapis.com/drive/v3/files/{TEMP_ID}?supportsAllDrives=true"

    def test_delete_swallows_exceptions(self):
        from chat.gemini_api.tool_handlers.drive import _delete_drive_file
        with patch("auth.google_credentials.make_authenticated_request", AsyncMock(side_effect=RuntimeError("x"))):
            assert _run(_delete_drive_file(USER, TEMP_ID)) is False


# ---------------------------------------------------------------------------
# google_export_doc cross-pointer
# ---------------------------------------------------------------------------


class TestExportDocCrossPointer:
    def _export(self, tmp_path, metadata):
        req = AsyncMock(return_value=metadata)
        with patch("chat.gemini_api.authed_get._make_authed_request", req), _patch_workspace(tmp_path):
            return json.loads(_run(_handle_google_export_doc(USER, "conv-1", FILE_ID, "pdf")))

    def test_docx_source_points_to_convert_tool(self, tmp_path):
        result = self._export(tmp_path, json.dumps({"name": "Q3.docx", "mimeType": DOCX_MIME}))
        assert "google_convert_document" in result["error"]
        assert f'"file_id": "{FILE_ID}", "format": "pdf"' in result["error"]
        assert "download_drive_file" in result["error"]

    def test_pdf_source_still_points_to_download_only(self, tmp_path):
        result = self._export(tmp_path, json.dumps({"name": "scan.pdf", "mimeType": "application/pdf"}))
        assert "google_convert_document" not in result["error"]
        assert "download_drive_file" in result["error"]


# ---------------------------------------------------------------------------
# Wiring + instructions
# ---------------------------------------------------------------------------


class TestWiring:
    def test_registered_in_registry_and_dispatch_table(self):
        from chat.gemini_api.tool_dispatch import TOOL_CALL_HANDLERS
        from chat.llm.tool_schemas import TOOL_CALL_REGISTRY
        assert "google_convert_document" in TOOL_CALL_REGISTRY
        assert "google_convert_document" in TOOL_CALL_HANDLERS
        schema = TOOL_CALL_REGISTRY["google_convert_document"]
        assert schema["parameters"]["required"] == ["format"]
        assert set(schema["parameters"]["properties"]) == {"format", "path", "file_id", "filename", "intent_message"}
        assert set(schema["parameters"]["properties"]["format"]["enum"]) == set(GOOGLE_DOC_EXPORT_FORMATS)
        # Transient Drive temp file, deleted in the same call: not a mutation.
        assert not schema.get("mutating")

    def test_not_in_public_or_script_allow_lists(self):
        from chat.gemini_api.script_tool_call import SCRIPT_TOOL_CALL_ALLOWLIST
        from chat.llm.tool_schemas import PUBLIC_TOOL_CALL_ALLOWLIST
        assert "google_convert_document" not in PUBLIC_TOOL_CALL_ALLOWLIST
        assert "google_convert_document" not in SCRIPT_TOOL_CALL_ALLOWLIST

    def test_dispatch_forwards_arguments(self):
        from chat.gemini_api.tool_dispatch import _dispatch_tool_call
        handler = AsyncMock(return_value=json.dumps({"status": "success"}))
        with patch("chat.gemini_api.tool_dispatch._handle_google_convert_document", handler):
            result, _parts = _run(_dispatch_tool_call(
                app=None, provider=None, user=USER, conversation_id="conv-1",
                timezone="UTC", tool_name="tool_call",
                args={"tool_name": "google_convert_document",
                      "arguments": {"path": "a.docx", "format": "pdf",
                                    "filename": "a.pdf", "intent_message": "Convert"}},
            ))
        assert json.loads(result)["status"] == "success"
        handler.assert_awaited_once_with(
            USER, "conv-1", "pdf", path="a.docx", file_id=None, filename="a.pdf", project_id=None,
        )

    def test_tool_description_prefers_native_over_sandbox(self):
        from chat.llm.tool_schemas import TOOL_CALL_REGISTRY
        desc = TOOL_CALL_REGISTRY["google_convert_document"]["description"]
        assert "PREFER" in desc and "run_python" in desc and "LibreOffice" in desc

    def test_docs_skill_instructs_preference(self):
        from api.docs import get_instructions
        text = get_instructions("http://localhost")
        assert "google_convert_document" in text
        assert "NOT a conversion inside `run_python`" in text
        for ext in GOOGLE_DOC_IMPORT_FORMATS:
            assert f"`.{ext}`" in text

    def test_drive_skill_points_to_convert_tool(self):
        from api.drive import get_instructions
        assert "google_convert_document" in get_instructions("http://localhost")

    def test_workspace_skill_steers_away_from_sandbox_conversion(self):
        from chat.system_skills.catalog import _workspace_content
        text = _workspace_content("http://localhost", "")
        assert "google_convert_document" in text
        assert "does NOT belong in the sandbox" in text

    def test_system_prompts_mention_convert_tool(self):
        from chat.gemini_api import system_prompt as sp
        assert sp.__file__  # module import guard
        source = Path(sp.__file__).read_text()
        # Top-level rule + sub-agent rule.
        assert source.count("google_convert_document") >= 2
