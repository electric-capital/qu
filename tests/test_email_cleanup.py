"""Tests for the email markdown cleanup logic in api.gmail.clean_email_markdown()."""

import pytest

from api.gmail import (
    clean_email_markdown,
    convert_html_to_markdown,
    _INVISIBLE_UNICODE_CODEPOINTS,
)


# ---------------------------------------------------------------------------
# Unicode stripping tests
# ---------------------------------------------------------------------------

class TestZeroWidthCharactersRemoved:
    """Test 1: Common zero-width characters are removed from text."""

    def test_common_zero_width_chars_removed(self):
        text = "Hello\u200c \u200bWorld\u034f and\u00ad good\ufeff morning"
        result = clean_email_markdown(text)
        assert result == "Hello World and good morning"

    def test_visible_text_preserved(self):
        text = "Hello\u200cWorld"
        result = clean_email_markdown(text)
        assert result == "HelloWorld"


class TestAllTargetCharactersRemoved:
    """Test 2: Every character in the strip list is removed."""

    def test_all_target_characters_removed(self):
        # Build a string containing every invisible character between visible chars
        parts = []
        for i, cp in enumerate(sorted(_INVISIBLE_UNICODE_CODEPOINTS)):
            parts.append(f"a{chr(cp)}b")
        text = " ".join(parts)
        result = clean_email_markdown(text)
        # All invisible characters should be gone; only "ab" segments and spaces remain
        for cp in _INVISIBLE_UNICODE_CODEPOINTS:
            assert chr(cp) not in result, f"U+{cp:04X} was not removed"

    def test_only_invisible_chars_gives_empty(self):
        text = "".join(chr(cp) for cp in _INVISIBLE_UNICODE_CODEPOINTS)
        result = clean_email_markdown(text)
        assert result == ""


class TestNormalTextUnchanged:
    """Test 3: Normal text without special characters passes through unchanged."""

    def test_normal_ascii_text(self):
        text = "Hello, world! This is a normal email."
        assert clean_email_markdown(text) == text

    def test_normal_unicode_text(self):
        text = "Bonjour le monde! Привет мир! 你好世界!"
        assert clean_email_markdown(text) == text

    def test_text_with_normal_punctuation(self):
        text = "Price: $19.99 — 50% off (limited time only)"
        assert clean_email_markdown(text) == text


class TestCharactersInsideMarkdownStructure:
    """Test 4: Invisible chars inside markdown structure are removed without
    breaking the structure."""

    def test_inside_link_text(self):
        text = "[Click\u200c here](https://example.com)"
        result = clean_email_markdown(text)
        assert result == "[Click here](https://example.com)"

    def test_inside_image_alt(self):
        text = "![alt\u200b text](https://example.com/img.png)"
        result = clean_email_markdown(text)
        assert result == "![alt text](https://example.com/img.png)"

    def test_inside_bold(self):
        text = "**bold\u034f text**"
        result = clean_email_markdown(text)
        assert result == "**bold text**"

    def test_inside_italic(self):
        text = "*italic\u00ad text*"
        result = clean_email_markdown(text)
        assert result == "*italic text*"

    def test_inside_heading(self):
        text = "# Heading\ufeff text"
        result = clean_email_markdown(text)
        assert result == "# Heading text"


class TestEmptyStringInput:
    """Test 5: Empty string returns empty string."""

    def test_empty_string(self):
        assert clean_email_markdown("") == ""

    def test_none_like_empty(self):
        # Empty string is falsy; the function should handle it
        assert clean_email_markdown("") == ""


# ---------------------------------------------------------------------------
# Newline collapsing tests
# ---------------------------------------------------------------------------

class TestTripleNewlinesCollapse:
    """Test 6: Triple newlines collapse to double."""

    def test_triple_newlines(self):
        text = "line1\n\n\nline2"
        result = clean_email_markdown(text)
        assert result == "line1\n\nline2"


class TestManyNewlinesCollapse:
    """Test 7: Many newlines collapse to double."""

    def test_six_newlines(self):
        text = "line1\n\n\n\n\n\nline2"
        result = clean_email_markdown(text)
        assert result == "line1\n\nline2"

    def test_ten_newlines(self):
        text = "line1" + "\n" * 10 + "line2"
        result = clean_email_markdown(text)
        assert result == "line1\n\nline2"


class TestDoubleNewlinesPreserved:
    """Test 8: Double newlines (paragraph breaks) are preserved."""

    def test_double_newlines_preserved(self):
        text = "line1\n\nline2"
        result = clean_email_markdown(text)
        assert result == "line1\n\nline2"

    def test_multiple_paragraphs_preserved(self):
        text = "para1\n\npara2\n\npara3"
        result = clean_email_markdown(text)
        assert result == "para1\n\npara2\n\npara3"


class TestSingleNewlinesPreserved:
    """Test 9: Single newlines are preserved."""

    def test_single_newline_preserved(self):
        text = "line1\nline2"
        result = clean_email_markdown(text)
        assert result == "line1\nline2"


class TestWhitespaceOnlyLinesBetweenNewlines:
    """Test 10: Whitespace-only lines between newlines are collapsed."""

    def test_spaces_between_newlines(self):
        text = "line1\n \n \n \nline2"
        result = clean_email_markdown(text)
        assert result == "line1\n\nline2"

    def test_tabs_between_newlines(self):
        text = "line1\n\t\n\t\n\t\nline2"
        result = clean_email_markdown(text)
        assert result == "line1\n\nline2"

    def test_mixed_whitespace_between_newlines(self):
        text = "line1\n \t \n\n \nline2"
        result = clean_email_markdown(text)
        assert result == "line1\n\nline2"


class TestMixedNewlineContent:
    """Test 11: Realistic email markdown with mixed newline situations."""

    def test_mixed_newline_sections(self):
        text = (
            "# Newsletter Title\n\n"
            "First paragraph of content.\n\n"
            "Second paragraph.\n\n\n\n\n"
            "Third paragraph after excessive newlines.\n\n"
            "Fourth paragraph (double preserved).\n\n\n"
            "Fifth paragraph after triple."
        )
        result = clean_email_markdown(text)
        expected = (
            "# Newsletter Title\n\n"
            "First paragraph of content.\n\n"
            "Second paragraph.\n\n"
            "Third paragraph after excessive newlines.\n\n"
            "Fourth paragraph (double preserved).\n\n"
            "Fifth paragraph after triple."
        )
        assert result == expected


class TestTrailingWhitespaceStripped:
    """Test 12: Lines with trailing spaces/tabs have that whitespace removed."""

    def test_trailing_spaces_stripped(self):
        text = "line1   \nline2  \nline3"
        result = clean_email_markdown(text)
        assert result == "line1\nline2\nline3"

    def test_trailing_tabs_stripped(self):
        text = "line1\t\nline2\t\t\nline3"
        result = clean_email_markdown(text)
        assert result == "line1\nline2\nline3"

    def test_trailing_mixed_whitespace_stripped(self):
        text = "line1 \t \nline2\t \nline3"
        result = clean_email_markdown(text)
        assert result == "line1\nline2\nline3"


# ---------------------------------------------------------------------------
# Combined function tests
# ---------------------------------------------------------------------------

class TestBothCleanupsApplied:
    """Test 13: Both Unicode stripping and newline collapsing work together."""

    def test_combined_cleanup(self):
        text = "Hello\u200c World\n\n\n\n\nSecond\u200b paragraph\n\n\n\nThird\u034f line"
        result = clean_email_markdown(text)
        assert result == "Hello World\n\nSecond paragraph\n\nThird line"

    def test_combined_with_trailing_whitespace(self):
        text = "Hello\u200c   \n\n\n\nWorld\u200b   "
        result = clean_email_markdown(text)
        assert result == "Hello\n\nWorld"


class TestRealisticHtmlOutput:
    """Test 14: Run realistic HTML through convert_html_to_markdown() then clean_email_markdown()."""

    def test_realistic_gmail_newsletter(self):
        # HTML representative of Gmail newsletters: hidden preview text with
        # ZWNJ padding, lots of <br> tags, nested tables producing blank lines.
        html = """
        <html><body>
        <div style="display:none;font-size:1px;color:#ffffff;line-height:1px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;">
            Preview text here\u200c\u200c\u200c\u200c\u200c\u200c\u200c\u200c\u200c\u200c
            \u200c\u200c\u200c\u200c\u200c\u200c\u200c\u200c\u200c\u200c
            &zwnj;&zwnj;&zwnj;&zwnj;&zwnj;&zwnj;&zwnj;&zwnj;
        </div>
        <br><br><br>
        <h1>Spring Sale!</h1>
        <br><br><br>
        <p>Don't miss our amazing deals.</p>
        <br><br><br><br>
        <p>Shop now at <a href="https://example.com/shop">our store</a>.</p>
        <br><br>
        <p>Thanks for reading.</p>
        </body></html>
        """

        markdown_raw = convert_html_to_markdown(html)

        # The raw markdown will contain ZWNJ characters and excessive newlines
        result = clean_email_markdown(markdown_raw)

        # No invisible Unicode characters should remain
        for cp in _INVISIBLE_UNICODE_CODEPOINTS:
            assert chr(cp) not in result, f"U+{cp:04X} still present in output"

        # No runs of 3+ newlines should remain
        import re
        triple_newlines = re.findall(r'\n{3,}', result)
        assert triple_newlines == [], f"Triple+ newlines found: {triple_newlines}"

        # Visible content should be preserved
        assert "Spring Sale!" in result
        assert "amazing deals" in result
        assert "Shop now" in result
        assert "our store" in result
        assert "Thanks for reading" in result
