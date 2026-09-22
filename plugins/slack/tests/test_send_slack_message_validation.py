"""Format-validation tests for SendSlackMessageHandler.validate_params.

These tests pin the channel_id and thread_ts format checks added on top
of the existing required-field / unknown-key validation. The unknown-
key behaviour is already covered by
``tests/test_action_request_unknown_params.py``; this file focuses on
the new regex/prefix checks so a malformed Slack id surfaces as a
synchronous ``Invalid parameters: ...`` tool result on the model's same
turn instead of persisting to an action_requests row and exploding
inside ``execute()`` with an opaque Slack error.
"""

import pytest

from plugins.slack.handlers import SendSlackMessageHandler


# ---------------------------------------------------------------------------
# Happy paths -- the validator must round-trip a well-formed payload
# without rewriting the id (Slack ids are case-sensitive).
# ---------------------------------------------------------------------------


def test_accepts_channel_prefix():
    handler = SendSlackMessageHandler()
    out = handler.validate_params({"channel_id": "C0123456789", "message": "hi"})
    assert out == {"channel_id": "C0123456789", "message": "hi"}


def test_accepts_user_prefix_no_rewrite():
    handler = SendSlackMessageHandler()
    out = handler.validate_params({"channel_id": "U0123456789", "message": "hi"})
    # U-prefix must round-trip exactly; the conversations.open branch
    # inside execute() relies on the leading character.
    assert out["channel_id"] == "U0123456789"


def test_accepts_group_prefix():
    handler = SendSlackMessageHandler()
    out = handler.validate_params({"channel_id": "G0123456789", "message": "hi"})
    assert out["channel_id"] == "G0123456789"


def test_accepts_dm_prefix():
    handler = SendSlackMessageHandler()
    out = handler.validate_params({"channel_id": "D0123456789", "message": "hi"})
    assert out["channel_id"] == "D0123456789"


def test_accepts_enterprise_user_prefix():
    handler = SendSlackMessageHandler()
    out = handler.validate_params({"channel_id": "W0123456789", "message": "hi"})
    assert out["channel_id"] == "W0123456789"


def test_accepts_thread_ts_canonical_shape():
    handler = SendSlackMessageHandler()
    out = handler.validate_params({
        "channel_id": "C01234567",
        "message": "hi",
        "thread_ts": "1727991234.000200",
    })
    assert out["thread_ts"] == "1727991234.000200"


def test_empty_thread_ts_is_treated_as_absent():
    handler = SendSlackMessageHandler()
    out = handler.validate_params({
        "channel_id": "C01234567",
        "message": "hi",
        "thread_ts": "   ",
    })
    # Whitespace-only thread_ts must NOT trigger a format error and
    # must NOT appear in the validated dict.
    assert "thread_ts" not in out


# ---------------------------------------------------------------------------
# Existing rejections (regression guards).
# ---------------------------------------------------------------------------


def test_missing_channel_id_rejected():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({"message": "hi"})


def test_missing_message_rejected():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="message"):
        handler.validate_params({"channel_id": "C0123456789"})


def test_message_over_3000_chars_rejected():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="3000"):
        handler.validate_params({
            "channel_id": "C0123456789",
            "message": "x" * 3001,
        })


def test_unknown_top_level_key_rejected():
    """Regression guard: server-injected keys must not be in the allow-list."""
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="Unknown parameter for send_slack_message") as exc:
        handler.validate_params({
            "channel_id": "C0123456789",
            "message": "hi",
            "channel_name": "general",
        })
    assert "channel_name" in str(exc.value)


# ---------------------------------------------------------------------------
# New channel_id format rejections.
# ---------------------------------------------------------------------------


def test_rejects_hash_channel_alias():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({"channel_id": "#general", "message": "hi"})


def test_rejects_at_user_alias():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({"channel_id": "@alice", "message": "hi"})


def test_rejects_permalink_url():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({
            "channel_id": "https://example.slack.com/archives/C0123456/p17279912340002",
            "message": "hi",
        })


def test_rejects_lowercase_prefix():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({"channel_id": "c01234567", "message": "hi"})


def test_rejects_unknown_prefix():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({"channel_id": "X01234567", "message": "hi"})


def test_rejects_mixed_case_tail():
    handler = SendSlackMessageHandler()
    # `Cabc12345` has the right prefix but the tail mixes lower/upper
    # case characters which Slack ids never do.
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({"channel_id": "Cabc12345", "message": "hi"})


def test_rejects_too_short_id():
    handler = SendSlackMessageHandler()
    # Below the {8,} tail floor.
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({"channel_id": "C1234", "message": "hi"})


def test_rejects_raw_integer_channel_id():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="channel_id"):
        handler.validate_params({"channel_id": 1234567, "message": "hi"})


def test_error_message_truncates_long_offending_value():
    handler = SendSlackMessageHandler()
    long_bogus = "z" * 500  # no valid prefix, > 100 chars
    with pytest.raises(ValueError) as exc:
        handler.validate_params({"channel_id": long_bogus, "message": "hi"})
    # The raw 500-char string must not be echoed back in full.
    assert "z" * 500 not in str(exc.value)
    assert "..." in str(exc.value)


# ---------------------------------------------------------------------------
# New thread_ts format rejections.
# ---------------------------------------------------------------------------


def test_rejects_thread_ts_without_dot():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="thread_ts"):
        handler.validate_params({
            "channel_id": "C01234567",
            "message": "hi",
            "thread_ts": "1727991234",
        })


def test_rejects_thread_ts_permalink_style():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="thread_ts"):
        handler.validate_params({
            "channel_id": "C01234567",
            "message": "hi",
            "thread_ts": "p1727991234000200",
        })


def test_rejects_thread_ts_non_digit_microseconds():
    handler = SendSlackMessageHandler()
    with pytest.raises(ValueError, match="thread_ts"):
        handler.validate_params({
            "channel_id": "C01234567",
            "message": "hi",
            "thread_ts": "1727991234.0002ab",
        })
