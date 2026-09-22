"""Tests for per-conversation flag parsing (chat/conversation_flags.py).

Pure-function tests for ``parse_flags_line`` and ``is_flag_enabled``: matching
the ``%%flags[...]`` magic first line, comma lists, unknown-token drop, empty
``%%flags[]``, non-first-line literal text, stripping the line + single trailing
newline, and the empty-after-strip case. No DB, no LLM.
"""

from chat.conversation_flags import (
    FLAG_NESTED_SUBAGENTS,
    KNOWN_FLAGS,
    is_flag_enabled,
    parse_flags_line,
)


class TestParseFlagsLine:
    def test_single_known_flag(self):
        flags, stripped = parse_flags_line("%%flags[nested_subagents]\nDo the thing")
        assert flags == [FLAG_NESTED_SUBAGENTS]
        assert stripped == "Do the thing"

    def test_strips_line_and_single_trailing_newline(self):
        # Only the magic line + its own trailing newline is removed; the rest of
        # the body (including internal blank lines) is preserved verbatim.
        flags, stripped = parse_flags_line("%%flags[nested_subagents]\n\nSecond paragraph")
        assert flags == [FLAG_NESTED_SUBAGENTS]
        assert stripped == "\nSecond paragraph"

    def test_comma_list_with_unknown_dropped(self):
        # Unknown tokens are silently dropped; only KNOWN_FLAGS are recognized.
        flags, stripped = parse_flags_line(
            "%%flags[nested_subagents, made_up_flag, another_unknown]\nprompt"
        )
        assert flags == [FLAG_NESTED_SUBAGENTS]
        assert stripped == "prompt"

    def test_tokens_trimmed_and_lowercased(self):
        flags, stripped = parse_flags_line("%%flags[  NESTED_SUBAGENTS  ]\nhi")
        assert flags == [FLAG_NESTED_SUBAGENTS]
        assert stripped == "hi"

    def test_empty_brackets_strips_line_no_flags(self):
        flags, stripped = parse_flags_line("%%flags[]\nreal prompt")
        assert flags == []
        assert stripped == "real prompt"

    def test_all_unknown_strips_line_no_flags(self):
        flags, stripped = parse_flags_line("%%flags[nope, also_nope]\nreal prompt")
        assert flags == []
        assert stripped == "real prompt"

    def test_duplicate_flags_deduped_order_preserved(self):
        flags, _ = parse_flags_line(
            "%%flags[nested_subagents, nested_subagents]\nx"
        )
        assert flags == [FLAG_NESTED_SUBAGENTS]

    def test_non_first_line_is_literal(self):
        # A %%flags line that is NOT the first line is treated as literal text.
        msg = "Hello\n%%flags[nested_subagents]"
        flags, stripped = parse_flags_line(msg)
        assert flags == []
        assert stripped == msg

    def test_no_magic_line_returns_unchanged(self):
        msg = "Just a normal message with no flags"
        flags, stripped = parse_flags_line(msg)
        assert flags == []
        assert stripped == msg

    def test_empty_after_strip(self):
        # A flags-only first message strips to empty text -- the caller decides
        # whether to reject; the parser just reports the empty remainder.
        flags, stripped = parse_flags_line("%%flags[nested_subagents]")
        assert flags == [FLAG_NESTED_SUBAGENTS]
        assert stripped == ""

    def test_flags_only_with_trailing_newline(self):
        flags, stripped = parse_flags_line("%%flags[nested_subagents]\n")
        assert flags == [FLAG_NESTED_SUBAGENTS]
        assert stripped == ""

    def test_trailing_whitespace_after_brackets_tolerated(self):
        flags, stripped = parse_flags_line("%%flags[nested_subagents]   \nprompt")
        assert flags == [FLAG_NESTED_SUBAGENTS]
        assert stripped == "prompt"

    def test_inline_text_after_brackets_does_not_match(self):
        # The regex anchors on a clean line; trailing non-whitespace content on
        # the same line means it is NOT a flags line (treated as literal).
        msg = "%%flags[nested_subagents] inline text"
        flags, stripped = parse_flags_line(msg)
        assert flags == []
        assert stripped == msg


class TestIsFlagEnabled:
    def test_none_is_false(self):
        assert is_flag_enabled(None, FLAG_NESTED_SUBAGENTS) is False

    def test_empty_is_false(self):
        assert is_flag_enabled([], FLAG_NESTED_SUBAGENTS) is False

    def test_present(self):
        assert is_flag_enabled([FLAG_NESTED_SUBAGENTS], FLAG_NESTED_SUBAGENTS) is True

    def test_absent(self):
        assert is_flag_enabled(["something_else"], FLAG_NESTED_SUBAGENTS) is False


class TestKnownFlags:
    def test_nested_subagents_is_known(self):
        assert FLAG_NESTED_SUBAGENTS in KNOWN_FLAGS
