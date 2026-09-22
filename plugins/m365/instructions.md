# Microsoft 365 Mail (Outlook)

Read and work with the user's Outlook (Microsoft 365 / Office 365) email through dedicated `m365_*` tools invoked via `tool_call`, plus raw read-only Microsoft Graph access via `authed_get`. All of this requires Microsoft 365 to be connected in Settings > Data Connections.

Identity note: the connected Microsoft 365 mailbox can be a DIFFERENT address than the user's Quest login email.

## Tools

| Tool | Description |
|------|-------------|
| `m365_get_mail_messages` | Fetch 1-50 messages by Graph message ID, rendered as a markdown document with decoded body and key headers. Options: `include_html`, `include_urls` |
| `m365_list_mail_folders` | List mail folders (the Gmail-labels analog); pass `folder_id` to fetch one folder plus its child folders |
| `m365_get_mail_message_urls` | Look up full URLs for the numeric `(#N#)` identifiers substituted into message bodies |
| `m365_create_mail_draft` | Create a draft email (does NOT send). Body is markdown (`body_md`, rendered to HTML; plain-text `body` only on explicit request), with reply/forward threading and attachments |
| `m365_send_mail_to_self` | Send an email to the user's own mailbox immediately (subject auto-prefixed with [Quest], `body_md` rendered to HTML) |
| `m365_archive_mail_message` | Apply a "Quest archived" category and move the message to the Archive folder |
| `m365_save_mail_attachment` | Download a file attachment into the conversation workspace |

**Important:**
- The dedicated tools do NOT support search queries. To search or list emails, use the raw Graph API via `authed_get` (below), then fetch the returned message IDs with `m365_get_mail_messages`.
- `m365_get_mail_messages` has a **hard limit of 50 IDs per call** — split larger sets into multiple calls.
- By default, long URLs in HTML bodies are replaced with short identifiers like `(#1#)` to save tokens (a `Replaced URL count: N` header line appears when this happened). Pass `"include_urls": true` to keep full URLs inline.
- The tools return **markdown documents**, not JSON. Structured values needed for follow-up calls — the Graph message id, conversation id, `Message-ID` header, and each `attachmentId` — appear as labelled lines you can parse directly.
- **Graph message IDs change when a message moves between folders** (including archiving). Always use the most recently returned id.

## Searching and listing messages (via authed_get)

Use `authed_get` against `https://graph.microsoft.com/v1.0/...`. Authentication is automatic (the user's Microsoft 365 token is injected and refreshed as needed). Read-only: only the GET paths below are reachable.

| Graph URL | Description | Query parameters |
|-----------|-------------|------------------|
| `/v1.0/me` | Connected mailbox identity | `$select` |
| `/v1.0/me/messages` | List/search all messages | `$search`, `$filter`, `$top`, `$skip`, `$select`, `$orderby` |
| `/v1.0/me/messages/{id}` | Get one message (raw JSON) | `$select`, `$expand` |
| `/v1.0/me/messages/{id}/attachments` | List attachment metadata | `$select` |
| `/v1.0/me/mailFolders` | List top-level folders | `$top`, `$select` |
| `/v1.0/me/mailFolders/{id}` | Get a folder (id or well-known name) | `$select` |
| `/v1.0/me/mailFolders/{id}/messages` | List/search messages in one folder | same as `/me/messages` |
| `/v1.0/me/mailFolders/{id}/childFolders` | List child folders | `$top`, `$select` |

Well-known folder names usable in place of folder ids: `inbox`, `archive`, `drafts`, `sentitems`, `deleteditems`, `junkemail`, `outbox`.

**Always pass `$select=id,subject,from,receivedDateTime,isRead,hasAttachments` (or similar) when listing** — full message objects are large.

```
# 10 most recent inbox messages
tool_call(tool_name="authed_get", arguments={"url": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=10&$select=id,subject,from,receivedDateTime,isRead"})

# Unread messages
tool_call(tool_name="authed_get", arguments={"url": "https://graph.microsoft.com/v1.0/me/messages?$filter=isRead eq false&$top=25&$select=id,subject,from,receivedDateTime"})

# Full-text search (KQL syntax: from:, subject:, to:, body:, hasAttachments:true, received>=YYYY-MM-DD)
tool_call(tool_name="authed_get", arguments={"url": "https://graph.microsoft.com/v1.0/me/messages?$search=\"from:alice@example.com invoice\"&$top=25&$select=id,subject,from,receivedDateTime"})

# Messages since a date
tool_call(tool_name="authed_get", arguments={"url": "https://graph.microsoft.com/v1.0/me/messages?$filter=receivedDateTime ge 2026-08-01T00:00:00Z&$top=25&$select=id,subject,from,receivedDateTime"})
```

Search caveats:
- `$search` cannot be combined with `$filter` or `$orderby`, and returns at most 250 results (paged via `$skip` is NOT supported with `$search`; use `@odata.nextLink` from the response instead).
- `$filter` string values use single quotes: `$filter=from/emailAddress/address eq 'alice@example.com'`.
- List responses page via the `@odata.nextLink` URL — pass it straight back to `authed_get`.

**Workflow:** search for ids with `authed_get`, then fetch full content:

```
tool_call(tool_name="m365_get_mail_messages", arguments={"message_ids": ["ID_1", "ID_2"]})
```

## Message format

`m365_get_mail_messages` with one id returns a markdown document shaped like this:

```
Subject: <Subject or "(no subject)">
From: Ada <ada@example.com>
To: grace@example.com
Cc: somebody@example.com            (line omitted when empty)
Date: 2026-08-27T10:00:00Z
Message-ID: <CAA...@outlook.com>
Graph Message ID: AAMkAGI2...
Conversation ID: AAQkAGI2...
Categories: (none)
Read: no
Replaced URL count: 2               (present only when URLs were replaced)

---

## Body

...message body as markdown, long URLs replaced with (#1#)-style identifiers...

---

## Attachments

- **report.pdf** -- `application/pdf`, 12,345 bytes, attachmentId: `AAMkAGI2...`
```

Notes:
- `Message-ID:` is the RFC internet message header (rarely needed — Graph reply/forward threading uses the Graph message id instead).
- `Graph Message ID:` is the id for follow-up tool calls; `Conversation ID:` groups a thread (filter with `$filter=conversationId eq '...'`).
- With multiple ids the response is a single `# Outlook messages batch` document: a counts line, a `## Batch errors` section first when any id failed, then `===`-separated per-message sections in request order.
- With `"include_html": true` the body section becomes `## Body (HTML)` containing the raw HTML in a fenced block.

### URL lookup

When bodies contain `(#N#)` identifiers, resolve them with:

```
tool_call(tool_name="m365_get_mail_message_urls", arguments={"message_id": "GRAPH_MESSAGE_ID", "identifiers": [1, 2]})

# All mappings for a message (omit identifiers)
tool_call(tool_name="m365_get_mail_message_urls", arguments={"message_id": "GRAPH_MESSAGE_ID"})
```

## Create draft (m365_create_mail_draft)

Creates a draft in the user's Drafts folder. Does **not** send. Write the body as markdown in `body_md` (rendered to HTML). Use the plain-text `body` field instead only when the user explicitly asks for a plain-text email.

| Field | Required | Description |
|-------|----------|-------------|
| `to` | New drafts & forwards | Recipient address(es), comma-separated. Optional on replies (defaults to the original sender) |
| `subject` | New drafts | Optional on replies/forwards (defaults to RE:/FW: + original subject) |
| `body_md` | Conditional | Markdown body, rendered to HTML. **Use this by default**, even for short or simple emails; takes precedence over `body`. Remote images (`![alt](https://...)`) are replaced by their alt text and raw HTML is stripped -- use a regular link instead |
| `body` | Conditional | Plain text body. Only use instead of `body_md` when the user explicitly asks for a plain-text email. Required if `body_md` absent |
| `cc` / `bcc` | No | Comma-separated |
| `reply_to_message_id` | No | **Graph message id** of the message being replied to (NOT the Message-ID header). Threading is handled by Exchange automatically |
| `forward_of_message_id` | No | Graph message id being forwarded; the original attachments are copied automatically |
| `attachments` | No | Array of attachment objects (below), 25MB total |

**Attachment object:** `{"type": "workspace", "workspace_path": "output/results.csv"}` or `{"type": "outlook", "message_id": "...", "attachment_id": "..."}`, each with optional `filename` override. Workspace paths are the same paths `list_workspace_files` returns.

**Inline images:** attach the image file and reference it in `body_md` as `![caption](cid:<filename>)`. Every attachment gets a Content-ID equal to its filename (runs of characters outside `A-Za-z0-9._-` become `_`, so `my chart.png` is `cid:my_chart.png`); the draft result lists each attachment's `content_id`. Attached images are flagged inline so Outlook renders them in place.

**Important:** on replies and forwards your composed body **replaces** the auto-quoted original. If the draft should quote the original message, include the quoted text in your body yourself (fetch the original with `m365_get_mail_messages` first).

**Do not hard-wrap the text** at a fixed column width (in either field). Every newline is rendered as a line break, so wrapped lines show up as ragged, oddly broken paragraphs in modern mail clients. Write each paragraph as one unwrapped line and separate paragraphs with a blank line.

```
# Simple draft (markdown body, one unwrapped line per paragraph)
tool_call(tool_name="m365_create_mail_draft", arguments={"to": "recipient@example.com", "subject": "Hello", "body_md": "Hi Sam,\n\nThanks for the update -- I've reviewed the numbers and they look good to me.\n\nBest,\nAlex"})

# Reply draft (threading automatic; body replaces the auto-quote)
tool_call(tool_name="m365_create_mail_draft", arguments={"reply_to_message_id": "AAMkAGI2...", "body_md": "Thanks -- confirmed for Tuesday."})

# Forward with a note (original attachments come along automatically)
tool_call(tool_name="m365_create_mail_draft", arguments={"forward_of_message_id": "AAMkAGI2...", "to": "colleague@example.com", "body_md": "FYI, see below."})

# Draft with a workspace file attached
tool_call(tool_name="m365_create_mail_draft", arguments={"to": "recipient@example.com", "subject": "Analysis", "body_md": "Results attached.", "attachments": [{"type": "workspace", "workspace_path": "output/results.csv"}]})

# Draft with an inline image (referenced by cid:)
tool_call(tool_name="m365_create_mail_draft", arguments={"to": "recipient@example.com", "subject": "Weekly chart", "body_md": "This week's trend:\n\n![Weekly trend](cid:chart.png)", "attachments": [{"type": "workspace", "workspace_path": "output/chart.png"}]})

# Plain-text draft -- ONLY when the user explicitly asked for plain text
tool_call(tool_name="m365_create_mail_draft", arguments={"to": "recipient@example.com", "subject": "Hello", "body": "Draft body text"})
```

## Send email to self (m365_send_mail_to_self)

Sends immediately (not a draft) from the user's mailbox to itself — for delivering reports, summaries, or reminders. Subject is auto-prefixed with `[Quest]`; `body_md` is markdown rendered to HTML. Remote images (`![alt](https://...)`) are replaced by their alt text and raw HTML is stripped -- use a regular link instead.

```
tool_call(tool_name="m365_send_mail_to_self", arguments={"subject": "Daily Summary", "body_md": "# Daily Summary\n\n- **Meeting at 2pm** with the design team"})
```

## Archive (m365_archive_mail_message)

Applies a `Quest archived` category (so the user can find Quest-archived mail in Outlook) and moves the message to the Archive folder. The message is not deleted. The result includes the message's **new** Graph id.

```
tool_call(tool_name="m365_archive_mail_message", arguments={"message_id": "AAMkAGI2..."})
```

## Attachments (m365_save_mail_attachment)

Downloads a file attachment into the conversation workspace (default `outlook-attachments/<original name>`; override with `path`). Then read it with `get_workspace_file` (handles PDFs/images) or process it with `run_python` / `run_script`. 50MB limit; only file attachments (not attached emails or reference links).

```
tool_call(tool_name="m365_save_mail_attachment", arguments={"message_id": "AAMkAGI2...", "attachment_id": "AAMkAGI2...AAABEgAQA..."})
```

## From scripts (run_python / run_script)

Sandbox code can call the read-only tools through the local tool bridge with the injected `QUEST_PORT` / `QUEST_API_KEY`:

```python
import json, os, urllib.request

req = urllib.request.Request(
    f"http://localhost:{os.environ['QUEST_PORT']}/api/tool-call",
    data=json.dumps({
        "tool_name": "m365_get_mail_messages",
        "arguments": {"message_ids": ["AAMkAGI2..."]},
    }).encode(),
    headers={
        "Authorization": f"Bearer {os.environ['QUEST_API_KEY']}",
        "Content-Type": "application/json",
    },
)
print(urllib.request.urlopen(req).read().decode())
```

Available from scripts: `m365_get_mail_messages`, `m365_list_mail_folders`. Script calls carry no conversation context, so bodies keep full URLs (no `(#N#)` replacement).
