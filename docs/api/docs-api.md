# Docs API Documentation

This document describes how Quest accesses Google Docs content for reading documents, exports Docs into the conversation workspace as files, and converts regular documents (Word, ODT, RTF, HTML, text, Markdown) with Google Docs' own converter.

## Overview

Docs read operations use the `authed_get` tool to make authenticated GET requests directly to the Google Docs API v1 at `https://docs.googleapis.com/v1/...`. The `authed_get` handler in `chat/gemini_api/authed_get.py` matches the hostname, loads the user's Google Services OAuth credentials, and injects a Bearer token into each request. An `allowed_endpoints` validation mechanism restricts which API paths can be called, preventing access to arbitrary Docs API endpoints. Listing Google Docs uses the Google Drive API via `authed_get` with a `mimeType='application/vnd.google-apps.document'` filter (Drive is already registered in `_SERVICE_REGISTRY`).

## Authentication

Docs reads require the user to have connected Google Services. The `authed_get` handler loads credentials via `get_valid_service_credentials()` in `auth/google_credentials.py` and injects a Bearer token. If the user has not connected Google Services, the handler returns an error.

**OAuth scope required** (granted via the separate Google Services OAuth flow at `/auth/google-services`):
- `documents.readonly` - Read access to Google Docs content

The Google Docs entry in `_SERVICE_REGISTRY` sets `requires_user: True` (per-user OAuth credentials) and `retry_on_401: True` (automatic token refresh and retry on 401 responses).

## Document Read Access (via `authed_get`)

Docs reads use `authed_get` with the Google Docs API v1 at `docs.googleapis.com`. The `_SERVICE_REGISTRY` entry in `chat/gemini_api/authed_get.py` defines allowed endpoint patterns via regex validation -- requests to non-matching paths are rejected. See that file for the allowed endpoint list and `api/docs.py` (`get_instructions()`) for query parameter documentation provided to the LLM.

## Listing Google Docs (via Google Drive API)

The Google Docs API does not provide a list endpoint. Listing and searching for Google Docs uses the Google Drive API with a mimeType filter (`mimeType='application/vnd.google-apps.document'`), also via `authed_get`. The Drive API base URL is `https://www.googleapis.com/drive/v3`. See `api/docs.py` (`get_instructions()`) for query parameter details.

## Exporting a Doc to the Workspace (via `google_export_doc`)

Native Google Docs carry no downloadable bytes -- `alt=media` on `files/{id}` fails for Google Workspace files, so `download_drive_file` cannot fetch them. The `google_export_doc` tool call converts a Doc through the Drive v3 `files.export` endpoint and writes the result to the conversation (or project) workspace, where the model reads it back with `get_workspace_file` or the user downloads it from the file browser.

**Implementation:** `_handle_google_export_doc()` in `chat/gemini_api/tool_handlers/drive.py`, dispatched via `tool_call` from the `TOOL_CALL_HANDLERS` table in `chat/gemini_api/tool_dispatch.py`; schema in `TOOL_CALL_REGISTRY` (`chat/llm/tool_schemas.py`). Parameters: `document_id` (required), `format` (required), optional `filename`.

**Flow:**

1. Resolve `format` against `GOOGLE_DOC_EXPORT_FORMATS` (short name, case-insensitive, leading dot tolerated, or the exact export MIME type). Unknown formats return an `error` plus the `supported_formats` map without any upstream call.
2. Fetch `files/{id}?fields=name,mimeType&supportsAllDrives=true` via `_make_authed_request()`. Anything other than `application/vnd.google-apps.document` is rejected: regular files get a pointer to `download_drive_file`; other Workspace types (Sheets, Slides, ...) get a "not supported by this tool" error. The title supplies the default filename.
3. `GET files/{id}/export?mimeType=<export mime>` with `raw_response=True`. Upstream errors (incl. Google's 10 MB export cap) are returned as `error` with a hint to try a lighter format.
4. Write the bytes to `<workspace>/<filename>` (filename sanitized via `_sanitize_workspace_filename`, traversal-checked; default `<title><ext>`; overwrites like `download_drive_file`), then `_publish_file_list_changed()` so the file browser refreshes. The receipt carries `filename`, `size_bytes`, `format`, `export_mime_type`, `content_type`, `document_title`, and a `message` with the follow-up `get_workspace_file` call.

**Supported formats** (the complete Google Docs export set): `pdf`, `docx`, `odt`, `rtf`, `txt`, `md`, `html`, `epub`, `zip` (zipped HTML with images). The table in `api/docs.py` (`get_instructions()`, surfaced by the `system:docs` skill) shows the model each format's MIME type and steers it to `md`/`txt` when it needs to read the content itself.

**Allow-list interaction:** the Drive `_SERVICE_REGISTRY` entry in `chat/gemini_api/authed_get.py` gained the GET pattern `^/drive/v3/files/[^/]+/export$` so the handler can reuse `_make_authed_request()` (credential injection, 401 refresh). `handle_authed_get()` rejects that path when `output_file` is absent -- mirroring the `alt=media` block -- and points the model at `google_export_doc`; with `output_file` set the export lands under `.responses/` like any other body. Export works under the existing `drive.readonly` scope, no re-consent needed.

**Not allow-listed for public projects or the script bridge:** like `download_drive_file`, the tool stays out of `PUBLIC_TOOL_CALL_ALLOWLIST` and `SCRIPT_TOOL_CALL_ALLOWLIST`.

## Converting Regular Documents with Google Docs (via `google_convert_document`)

`google_export_doc` only accepts native Google Docs, so a `.docx` stored in Drive (or uploaded to the workspace) used to be downloaded and then converted to PDF inside the script sandbox. The sandbox image has no LibreOffice or Word, only `python-docx` and `pypdf`, and users reported the resulting PDFs as subpar. `google_convert_document` routes such conversions through Google Docs' own converter instead, producing the same output as File > Download in Google Docs.

**Implementation:** `_handle_google_convert_document()` in `chat/gemini_api/tool_handlers/drive.py`, dispatched via `tool_call` from `TOOL_CALL_HANDLERS`; schema in `TOOL_CALL_REGISTRY`. Parameters: `format` (required, the same nine targets as `google_export_doc`), exactly one of `path` (workspace file) / `file_id` (regular Drive file), optional `filename`.

**Flow:**

1. Resolve `format` (same resolver as `google_export_doc`); reject unless exactly one source is given; reject with a reconnect hint when the stored Google connection has scopes but lacks `drive.file` (the temporary Doc is created under that scope).
2. Get the source bytes. Drive source: `files/{id}?fields=name,mimeType,size` via `_make_authed_request()` -- a native Google Doc is refused with a pointer to `google_export_doc`, other Workspace types are unsupported, a non-importable type (e.g. a PDF) is refused with a pointer to `download_drive_file`, an over-cap declared `size` short-circuits before the download -- then `alt=media`. Workspace source: containment-checked path under the conversation/project workspace, must be a regular file. The import MIME type comes from `_resolve_doc_import_mime()`: file extension first (`GOOGLE_DOC_IMPORT_FORMATS`: `doc`, `docx`, `odt`, `rtf`, `txt`, `html`/`htm`, `md`), Drive-reported mimeType as fallback. Empty sources and sources over `GOOGLE_DOC_IMPORT_MAX_BYTES` (50 MB, Google's conversion cap) are refused.
3. `_import_as_google_doc()`: multipart `POST upload/drive/v3/files?uploadType=multipart` via `make_authenticated_request()` with metadata `{"name": "[Quest temp] <stem>", "mimeType": application/vnd.google-apps.document}` and the source bytes under the import MIME type, so Drive converts on import (the same call shape as the file browser's Save to Drive). Scope/permission 401/403s map to the shared Drive reauth message; the Drive upload host is not in the `authed_get` allow-list, hence the direct call.
4. `GET files/{temp id}/export?mimeType=<target>` via `_make_authed_request()` (the existing allow-listed export path), wrapped in `try/finally` so `_delete_drive_file()` (`DELETE files/{temp id}`, 200/204/404 = gone) always runs -- also when the export raises.
5. Write the bytes to `<workspace>/<filename>` (default `<source stem><ext>`, sanitized + traversal-checked), `_publish_file_list_changed()`. The receipt carries `filename`, `size_bytes`, `format`, `export_mime_type`, `content_type`, `source` (`path` or `file_id` + `name` + `import_mime_type`), `temporary_google_doc` (`id`, `deleted`) and a `message`; when the delete failed a `warning` names the leftover Doc's URL so the model tells the user. An export failure returns `error` + the 10 MB hint plus the same temp-Doc fields.

**Classification:** not `mutating` -- on success nothing outside the workspace changes (the temp Doc exists only for the duration of the call), so one-shot inference API runs may use it; pinned in `tests/test_inference_api.py`. Not in `PUBLIC_TOOL_CALL_ALLOWLIST` or `SCRIPT_TOOL_CALL_ALLOWLIST`, like the other Drive tools.

**Model steering:** the tool description, the `system:docs` skill (`api/docs.py`), the `system:drive` download notes (`api/drive.py`), the `system:workspace` sandbox notes (`chat/system_skills/catalog.py`) and both the top-level and sub-agent system prompts (`chat/gemini_api/system_prompt.py`) tell the model to prefer `google_convert_document` over any conversion inside `run_python` / `run_script`, and to fall back to the sandbox only when Google Services is not connected (saying so to the user). `google_export_doc`'s non-Doc rejection now points at `google_convert_document` when the file is an importable document, and at `download_drive_file` otherwise.

**Scopes:** works with the existing `drive.readonly` + `drive.file` grant, no re-consent. `files.copy` with on-copy conversion would avoid the download/upload round trip, but it is not authorized by `drive.readonly` and under `drive.file` the source must be app-created, so the import route is the one that works for arbitrary user files.

## Design Decisions

**Why import + export + delete instead of converting in the sandbox?**
Fidelity. Google's converter handles layout, fonts, images, headers/footers, tables and page breaks the way Docs renders them; the sandbox has no office suite and code-based conversion cannot match that. The temporary Doc is the price of using Google's converter on a file that is not already a Doc.

**Why no approval card for the temporary Drive write?**
The write is transient and self-reverting: the Doc is app-owned, named `[Quest temp] ...`, and deleted in the same call (also on export failure or exception). A leftover only happens when the delete itself fails, which the receipt surfaces as a warning. An approval round trip for every conversion would push the model back to the sandbox path this tool exists to replace.

**Why a dedicated `google_export_doc` tool instead of reusing `download_drive_file` with a format parameter?**
`download_drive_file` is a byte-for-byte fetch (`alt=media`) whose contract is "the file as stored"; export is a conversion with a format choice, a different endpoint, a Docs-only precondition, and different failure modes (the 10 MB cap). Keeping them separate keeps each tool's description short and unambiguous for the model, and the mimeType pre-check gives a precise cross-pointer in each direction.


**Why `authed_get` instead of proxy endpoints?**
Docs reads are standard Google Docs API GET requests. Using `authed_get` with per-user OAuth support eliminates the need for dedicated proxy endpoints in `quest.py`, reduces backend code, and follows the same pattern used for Google Calendar, Google Drive, and other external API services. The `authed_get` handler already provides credential injection, hostname-based service matching, and 401 retry logic.

**Why use the Drive API for listing documents?**
The Google Docs API does not provide a list endpoint. The Drive API is used with a mimeType filter to return only Google Docs. This follows Google's recommended pattern for discovering documents.

**Why a separate hostname (`docs.googleapis.com`) instead of path-prefix on `www.googleapis.com`?**
Unlike Google Calendar and Drive (which live at `www.googleapis.com/calendar/v3` and `www.googleapis.com/drive/v3`), the Google Docs API uses its own hostname (`docs.googleapis.com`). The `_SERVICE_REGISTRY` entry uses plain hostname matching without a `path_prefix` field.
