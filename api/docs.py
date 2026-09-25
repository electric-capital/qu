"""Google Docs API instructions for the system prompt.

Docs read operations are handled via the ``authed_get`` tool, which
makes authenticated GET requests directly to the Google Docs API at
``https://docs.googleapis.com/v1/...``.

Listing Google Docs uses the Google Drive API
(``https://www.googleapis.com/drive/v3/files``) with a mimeType filter,
which is also accessed via ``authed_get`` (Drive is already registered).

Exporting a Doc to a file (PDF, DOCX, Markdown, ...) uses the
``google_export_doc`` tool call (``_handle_google_export_doc`` in
``chat/gemini_api/tool_handlers/drive.py``), which drives the Drive
``files.export`` endpoint and writes the result to the workspace.

Converting a regular document (a ``.docx`` on Drive or in the workspace)
with Google Docs' converter uses the ``google_convert_document`` tool call
(``_handle_google_convert_document`` in the same module): import as a
temporary Google Doc, export, delete the temporary Doc.
"""

DOCS_API_BASE = "https://docs.googleapis.com/v1"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


# ---------------------------------------------------------------------------
# Instruction text for the Docs API
# ---------------------------------------------------------------------------

def get_instructions(base_url: str) -> str:
    """Docs API documentation section (accessed via authed_get)."""
    return f"""## Google Docs API (via authed_get)

Access the Google Docs API using `authed_get` with the full Google Docs API URL. Authentication is handled automatically.

**Base URL:** `{DOCS_API_BASE}`

**Key API Paths:**

| Path | Description |
|------|-------------|
| `/v1/documents/{{documentId}}` | Get a Google Doc by ID (full document content) |

**Get Document Parameters:**
- `includeTabsContent`: Include content from all tabs (boolean, e.g., `true`)
- `fields`: Fields to include in response (e.g., 'title,body'). Use to reduce response size and save tokens.

**Listing Google Docs (via Google Drive API):**

To list or search for Google Docs, use the Google Drive API with a mimeType filter. The Drive API base URL is `{DRIVE_API_BASE}`.

| Path | Description |
|------|-------------|
| `/drive/v3/files` | List/search files (use `q` parameter to filter by mimeType) |

**List Parameters:**
- `q`: Query string (must include `mimeType='application/vnd.google-apps.document'` to filter for Docs)
- `pageSize`: Maximum number of files per page (1-1000, default 100)
- `pageToken`: Token for pagination
- `orderBy`: Sort order (e.g., 'modifiedTime desc', 'name')
- `fields`: Fields to include in response (e.g., 'files(id,name,modifiedTime)'). Use to reduce response size and save tokens.

**Example tool calls:**

```
# Get a document by ID
tool_call(tool_name="authed_get", arguments={{"url": "{DOCS_API_BASE}/documents/DOCUMENT_ID"}})

# Get a document with all tabs content
tool_call(tool_name="authed_get", arguments={{"url": "{DOCS_API_BASE}/documents/DOCUMENT_ID?includeTabsContent=true"}})

# Get only the document title
tool_call(tool_name="authed_get", arguments={{"url": "{DOCS_API_BASE}/documents/DOCUMENT_ID?fields=title"}})

# List all Google Docs (via Drive API)
tool_call(tool_name="authed_get", arguments={{"url": "{DRIVE_API_BASE}/files?q=mimeType%20%3D%20%27application%2Fvnd.google-apps.document%27"}})

# List Google Docs with name search
tool_call(tool_name="authed_get", arguments={{"url": "{DRIVE_API_BASE}/files?q=mimeType%20%3D%20%27application%2Fvnd.google-apps.document%27%20and%20name%20contains%20%27report%27"}})

# List recent Google Docs
tool_call(tool_name="authed_get", arguments={{"url": "{DRIVE_API_BASE}/files?q=mimeType%20%3D%20%27application%2Fvnd.google-apps.document%27&orderBy=modifiedTime%20desc&pageSize=10"}})

# List Google Docs with specific fields
tool_call(tool_name="authed_get", arguments={{"url": "{DRIVE_API_BASE}/files?q=mimeType%20%3D%20%27application%2Fvnd.google-apps.document%27&fields=files(id,name,modifiedTime)"}})
```

**Exporting a Google Doc to the workspace (via google_export_doc):**

Google Docs have no raw bytes, so `download_drive_file` cannot fetch them. To get a document as a file, use the `google_export_doc` tool call. It converts the Doc through the Drive `files.export` endpoint, writes the result to the conversation workspace, and returns the filename so you can read it with `get_workspace_file`.

Supported `format` values (every export format Google Docs supports):

| format | Output | Export MIME type |
|--------|--------|------------------|
| `pdf` | PDF | `application/pdf` |
| `docx` | Microsoft Word | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `odt` | OpenDocument Text | `application/vnd.oasis.opendocument.text` |
| `rtf` | Rich Text | `application/rtf` |
| `txt` | Plain text | `text/plain` |
| `md` | Markdown | `text/markdown` |
| `html` | HTML (single page) | `text/html` |
| `epub` | EPUB e-book | `application/epub+zip` |
| `zip` | Zipped HTML with images | `application/zip` |

```
# Export a Doc as PDF (saved as "<document title>.pdf")
tool_call(tool_name="google_export_doc", arguments={{"document_id": "DOCUMENT_ID", "format": "pdf"}})

# Export as Markdown under a chosen filename, then read it back
tool_call(tool_name="google_export_doc", arguments={{"document_id": "DOCUMENT_ID", "format": "md", "filename": "spec.md"}})
tool_call(tool_name="get_workspace_file", arguments={{"path": "spec.md"}})

# Export as Word for the user to download from the file browser
tool_call(tool_name="google_export_doc", arguments={{"document_id": "DOCUMENT_ID", "format": "docx"}})
```

- Prefer `md` or `txt` when YOU need to read the content -- they are the cheapest to load back into context. Use `pdf` / `docx` / `odt` / `rtf` / `epub` when the user wants a file to keep, share, or upload elsewhere (e.g. via `upload_to_drive`).
- The tool only exports native Google Docs (`mimeType = application/vnd.google-apps.document`). Regular files (uploaded PDFs, Word files, ...) go through `download_drive_file`; Sheets and Slides are not supported by this tool.
- Google caps exports at 10 MB of exported content; very large documents may fail in heavy formats and need `txt` / `md`.
- Do NOT call `/drive/v3/files/{{id}}/export` through `authed_get` -- the tool call path rejects it and points you back here.

**Converting Word / ODT / RTF / HTML / text / Markdown documents with Google Docs' converter (via google_convert_document):**

When the user wants a PDF (or docx / odt / rtf / txt / md / html / epub / zip) made from a regular document -- a `.docx` stored in Google Drive, a Word file uploaded to the workspace, an `.odt` / `.rtf` / `.html` / `.txt` / `.md` file -- **use `google_convert_document`, NOT a conversion inside `run_python` / `run_script`.** The sandbox has no LibreOffice or Word: building a PDF from `python-docx` output loses layout, fonts, images, headers/footers and tables, and users have reported the results as subpar. `google_convert_document` produces the same output as File > Download in Google Docs. Under the hood it imports the file as a temporary Google Doc (Drive converts on import), exports it in the requested format, writes the result to the workspace, and deletes the temporary Doc again -- nothing is left in the user's Drive, and no approval card is needed.

Pass exactly one source: `path` (workspace file) or `file_id` (regular Drive file). Supported source types: `.docx`, `.doc`, `.odt`, `.rtf`, `.txt`, `.html` / `.htm`, `.md`. Target `format` values are the same nine as `google_export_doc`.

```
# A Word document stored in Google Drive -> PDF (no download step needed; the
# Drive file id is enough)
tool_call(tool_name="google_convert_document", arguments={{"file_id": "DRIVE_FILE_ID", "format": "pdf"}})

# A Word document the user uploaded to the workspace -> PDF
tool_call(tool_name="google_convert_document", arguments={{"path": "Q3 Report.docx", "format": "pdf"}})

# Markdown you wrote to the workspace -> a nicely typeset PDF for the user
tool_call(tool_name="google_convert_document", arguments={{"path": "summary.md", "format": "pdf", "filename": "Summary.pdf"}})

# .doc / .odt / .rtf -> .docx
tool_call(tool_name="google_convert_document", arguments={{"path": "legacy.doc", "format": "docx"}})
```

- The result lands in the workspace (default name: source name with the new extension, e.g. `Q3 Report.pdf`). The user downloads it from the file browser; attach it elsewhere via `upload_to_drive`, `create_gmail_draft`, `send_slack_dm_to_self`, etc.
- Native Google Docs are already Docs -- export them with `google_export_doc` (the tool tells you so if you pass a Doc's id). Google Sheets / Slides are not supported.
- Limits: sources up to 50 MB (Google's conversion cap); exports up to 10 MB of exported content. Conversion fidelity is Google Docs' -- complex Word features Docs itself doesn't support (macros, some advanced layout) are simplified the same way opening the file in Docs would.
- Only fall back to converting in the sandbox when Google Services is not connected (the tool returns a `google_services_auth_required` error) -- and say so to the user, since the sandbox output will be lower quality.

**Important Notes:**
- Document IDs can be found in Google Docs URLs (e.g., `https://docs.google.com/document/d/DOCUMENT_ID/edit`)
- To list Google Docs, you MUST include `mimeType='application/vnd.google-apps.document'` in the `q` parameter of the Drive API query. Use URL encoding for query strings (spaces become %20, quotes become %27).
- Use the `fields` parameter to request only the data you need, which reduces response size and saves tokens."""
