"""Tests for render_batch_markdown() in api.gmail.helpers.

The batch renderer takes an ordered list of per-id result entries and
produces a single markdown document with a header, optional batch-errors
section, and ``===``-separated per-message sections.
"""

from api.gmail import render_batch_markdown


def _success_entry(id_: str, markdown_body: str = "") -> dict:
    if not markdown_body:
        markdown_body = f"Subject: Message {id_}\nFrom: sender\n\n## Body\n\nBody for {id_}"
    return {"id": id_, "ok": True, "markdown": markdown_body}


def _error_entry(id_: str, code: str = "not_found", msg: str = "Message not found") -> dict:
    return {"id": id_, "ok": False, "error_code": code, "error_message": msg}


# ---------------------------------------------------------------------------

def test_all_success_batch_emits_no_errors_section():
    entries = [_success_entry("A"), _success_entry("B")]
    out = render_batch_markdown(entries)

    assert out.startswith("# Gmail messages batch\n")
    assert "Fetched 2 message(s). 0 error(s)." in out
    assert "## Batch errors" not in out
    # Exactly two `===` separators (one before each success).
    assert out.count("\n===\n") == 2
    # Both successes' markdown bodies appear in order.
    assert "Body for A" in out
    assert "Body for B" in out
    assert out.index("Body for A") < out.index("Body for B")


def test_mixed_success_and_errors_batch_puts_errors_first():
    entries = [
        _success_entry("A"),
        _error_entry("B"),
        _success_entry("C"),
    ]
    out = render_batch_markdown(entries)

    assert "Fetched 2 message(s). 1 error(s)." in out
    assert "## Batch errors" in out
    # Errors section must appear before any per-message separator / section.
    errors_idx = out.index("## Batch errors")
    first_sep_idx = out.index("\n===\n")
    assert errors_idx < first_sep_idx
    # Error bullet format
    assert "- **B** -- `not_found`: Message not found" in out
    # Successes preserved in request order
    assert "Body for A" in out
    assert "Body for C" in out
    assert out.index("Body for A") < out.index("Body for C")


def test_all_failures_batch_has_only_errors_section():
    entries = [
        _error_entry("X", "not_found", "Message not found"),
        _error_entry("Y", "access_denied", "Access denied to this message"),
        _error_entry("Z", "invalid_id", "Invalid message ID format"),
    ]
    out = render_batch_markdown(entries)

    assert "Fetched 0 message(s). 3 error(s)." in out
    assert "## Batch errors" in out
    assert "===" not in out
    # All three error bullets are present in order.
    x_idx = out.index("**X**")
    y_idx = out.index("**Y**")
    z_idx = out.index("**Z**")
    assert x_idx < y_idx < z_idx
    assert "`access_denied`" in out
    assert "Invalid message ID format" in out


def test_batch_preserves_request_id_order_for_both_errors_and_messages():
    # Request order: B, A, C; B fails, A and C succeed.
    entries = [
        _error_entry("B"),
        _success_entry("A"),
        _success_entry("C"),
    ]
    out = render_batch_markdown(entries)

    assert "Fetched 2 message(s). 1 error(s)." in out
    # Error bullet for B appears (it's the only error, so trivially first in
    # its list).
    assert "- **B** -- `not_found`: Message not found" in out
    # Successes appear in the original request order (A before C).
    assert out.index("Body for A") < out.index("Body for C")


def test_single_success_still_uses_separator():
    entries = [_success_entry("SOLO")]
    out = render_batch_markdown(entries)

    assert "Fetched 1 message(s). 0 error(s)." in out
    assert "\n===\n" in out
    assert "Body for SOLO" in out
