"""Tests for the URL replacement logic in api.gmail.replace_urls_with_identifiers()."""

import pytest

from api.gmail import (
    replace_urls_with_identifiers,
    _URL_REPLACEMENT_MIN_LENGTH,
    convert_html_to_markdown,
    clean_email_markdown,
    _INVISIBLE_UNICODE_CODEPOINTS,
)


# ---------------------------------------------------------------------------
# Helper: generate a URL of a specific length
# ---------------------------------------------------------------------------

def _make_url(length: int, prefix: str = "https://example.com/") -> str:
    """Return a URL of exactly `length` characters."""
    assert length >= len(prefix), f"length must be >= {len(prefix)}"
    padding = length - len(prefix)
    return prefix + "x" * padding


# Convenience URLs
LONG_URL = _make_url(_URL_REPLACEMENT_MIN_LENGTH + 20)
LONG_URL_2 = _make_url(_URL_REPLACEMENT_MIN_LENGTH + 30, "https://other.example.com/")
LONG_IMG_URL = _make_url(_URL_REPLACEMENT_MIN_LENGTH + 25, "https://img.example.com/")
LONG_LINK_URL = _make_url(_URL_REPLACEMENT_MIN_LENGTH + 35, "https://track.example.com/")
SHORT_URL = "https://example.com/short"

# Sanity-check our helpers
assert len(LONG_URL) >= _URL_REPLACEMENT_MIN_LENGTH
assert len(SHORT_URL) < _URL_REPLACEMENT_MIN_LENGTH


# ---------------------------------------------------------------------------
# Regression tests for existing behaviour
# ---------------------------------------------------------------------------

class TestSimpleLinkReplacement:
    """Test 1: Basic [text](long-url) is replaced."""

    def test_simple_link_replacement(self):
        text = f"Click [here]({LONG_URL}) to visit."
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_URL not in result
        assert "[here](#1#)" in result
        assert mapping == {1: LONG_URL}


class TestSimpleImageReplacement:
    """Test 2: ![alt](long-url) is replaced to ![alt](#N#)."""

    def test_simple_image_replacement(self):
        text = f"![banner]({LONG_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_URL not in result
        assert "![banner](#1#)" in result
        assert mapping == {1: LONG_URL}


class TestBoldedLinkReplacement:
    """Test 3: **[text](long-url)** is replaced to **[text](#N#)**."""

    def test_bolded_link_replacement(self):
        text = f"**[Click here]({LONG_URL})**"
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_URL not in result
        assert "**[Click here](#1#)**" in result
        assert mapping == {1: LONG_URL}


class TestBareUrlReplacement:
    """Test 4: Standalone https://long-url is replaced to (#N#)."""

    def test_bare_url_replacement(self):
        text = f"Visit {LONG_URL} for details."
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_URL not in result
        assert "(#1#)" in result
        assert mapping == {1: LONG_URL}


# ---------------------------------------------------------------------------
# Primary bug fix tests: linked images
# ---------------------------------------------------------------------------

class TestLinkedImageBothUrlsReplaced:
    """Test 5: [ ![alt](long-img-url) ](long-link-url) -> both URLs replaced."""

    def test_linked_image_both_urls_replaced(self):
        text = f"[ ![alt text]({LONG_IMG_URL}) ]({LONG_LINK_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_IMG_URL not in result
        assert LONG_LINK_URL not in result
        assert "[![alt text](#1#)](#2#)" in result
        assert mapping[1] == LONG_IMG_URL
        assert mapping[2] == LONG_LINK_URL


class TestLinkedImageCompactForm:
    """Test 6: [![alt](long-img-url)](long-link-url) -> both URLs replaced."""

    def test_linked_image_compact_form(self):
        text = f"[![alt text]({LONG_IMG_URL})]({LONG_LINK_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_IMG_URL not in result
        assert LONG_LINK_URL not in result
        assert "[![alt text](#1#)](#2#)" in result
        assert mapping[1] == LONG_IMG_URL
        assert mapping[2] == LONG_LINK_URL


class TestLinkedImageOnlyOuterUrlLong:
    """Test 7: [![alt](short-url)](long-url) -> only outer URL replaced."""

    def test_linked_image_only_outer_url_long(self):
        text = f"[![alt]({SHORT_URL})]({LONG_LINK_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert SHORT_URL in result
        assert LONG_LINK_URL not in result
        assert f"[![alt]({SHORT_URL})](#1#)" in result
        assert mapping == {1: LONG_LINK_URL}


class TestLinkedImageOnlyInnerUrlLong:
    """Test 8: [![alt](long-url)](short-url) -> only inner URL replaced."""

    def test_linked_image_only_inner_url_long(self):
        text = f"[![alt]({LONG_IMG_URL})]({SHORT_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_IMG_URL not in result
        assert SHORT_URL in result
        assert f"[![alt](#1#)]({SHORT_URL})" in result
        assert mapping == {1: LONG_IMG_URL}


class TestLinkedImageEmptyAlt:
    """Test 9: [![](long-img-url)](long-link-url) -> both replaced, empty alt preserved."""

    def test_linked_image_empty_alt(self):
        text = f"[![]({LONG_IMG_URL})]({LONG_LINK_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_IMG_URL not in result
        assert LONG_LINK_URL not in result
        assert "[![](#1#)](#2#)" in result
        assert mapping[1] == LONG_IMG_URL
        assert mapping[2] == LONG_LINK_URL

    def test_linked_image_empty_alt_spaced_form(self):
        """Converter form with spaces inside the outer link."""
        text = f"[ ![]({LONG_IMG_URL}) ]({LONG_LINK_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_IMG_URL not in result
        assert LONG_LINK_URL not in result
        assert "[![](#1#)](#2#)" in result


class TestLinkedImageSameUrl:
    """Test 10: Both URLs identical -> share same identifier."""

    def test_linked_image_same_url(self):
        text = f"[![alt]({LONG_URL})]({LONG_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert LONG_URL not in result
        assert "[![alt](#1#)](#1#)" in result
        assert mapping == {1: LONG_URL}


# ---------------------------------------------------------------------------
# Threshold and dedup tests
# ---------------------------------------------------------------------------

class TestUrlBelowThresholdNotReplaced:
    """Test 11: URLs shorter than threshold are untouched."""

    def test_url_below_threshold_not_replaced(self):
        text = f"[link]({SHORT_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert result == text
        assert mapping == {}

    def test_bare_short_url_not_replaced(self):
        text = f"Visit {SHORT_URL} for info."
        result, mapping = replace_urls_with_identifiers(text)
        assert result == text
        assert mapping == {}


class TestDuplicateUrlSharesIdentifier:
    """Test 12: Same long URL in multiple places gets same #N#."""

    def test_duplicate_url_shares_identifier(self):
        text = f"[one]({LONG_URL}) and [two]({LONG_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert result == "[one](#1#) and [two](#1#)"
        assert mapping == {1: LONG_URL}


class TestNoLongUrlsReturnsOriginal:
    """Test 13: Input with only short URLs returns original text and empty mapping."""

    def test_no_long_urls_returns_original(self):
        text = f"Hello [link]({SHORT_URL}) world"
        result, mapping = replace_urls_with_identifiers(text)
        assert result == text
        assert mapping == {}

    def test_no_urls_at_all(self):
        text = "Just plain text with no URLs at all."
        result, mapping = replace_urls_with_identifiers(text)
        assert result == text
        assert mapping == {}


# ---------------------------------------------------------------------------
# Mixed content test
# ---------------------------------------------------------------------------

class TestMixedContent:
    """Test 14: Realistic email body with various link types."""

    def test_mixed_content(self):
        text = (
            f"# Newsletter\n\n"
            f"[ ![hero]({LONG_IMG_URL}) ]({LONG_LINK_URL})\n\n"
            f"Read more at [this article]({LONG_URL}).\n\n"
            f"**[Subscribe now]({LONG_URL_2})**\n\n"
            f"![small logo]({SHORT_URL})\n\n"
            f"Visit {LONG_URL} for details.\n"
        )
        result, mapping = replace_urls_with_identifiers(text)
        # All long URLs should be replaced
        assert LONG_IMG_URL not in result
        assert LONG_LINK_URL not in result
        assert LONG_URL not in result
        assert LONG_URL_2 not in result
        # Short URL should be preserved
        assert SHORT_URL in result
        # Check identifiers are assigned in order of first appearance
        assert mapping[1] == LONG_IMG_URL
        assert mapping[2] == LONG_LINK_URL
        assert mapping[3] == LONG_URL
        assert mapping[4] == LONG_URL_2
        # LONG_URL appears twice (in link and bare), but should share identifier
        assert result.count("#3#") == 2


# ---------------------------------------------------------------------------
# Link text edge cases
# ---------------------------------------------------------------------------

class TestLinkTextEqualsUrl:
    """Test 15: [https://long-url](https://long-url) -> simplified to [link](#N#)."""

    def test_link_text_equals_url(self):
        text = f"[{LONG_URL}]({LONG_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert "[link](#1#)" in result
        assert mapping == {1: LONG_URL}


class TestEmptyLinkText:
    """Test 16: [](long-url) -> becomes [link](#N#)."""

    def test_empty_link_text(self):
        text = f"[]({LONG_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert "[link](#1#)" in result
        assert mapping == {1: LONG_URL}


class TestImageEmptyAltStandalone:
    """Test 17: ![](long-url) -> becomes ![](#N#) (not ![link](#N#))."""

    def test_image_empty_alt_standalone(self):
        text = f"![]({LONG_URL})"
        result, mapping = replace_urls_with_identifiers(text)
        assert result == f"![](#1#)"
        assert "![link]" not in result
        assert mapping == {1: LONG_URL}


# ---------------------------------------------------------------------------
# Realistic newsletter conversion test
# ---------------------------------------------------------------------------

class TestRealisticNewsletterConversion:
    """Test 18: Feed realistic newsletter HTML through convert_html_to_markdown(), then through
    replace_urls_with_identifiers(), verify no long URLs remain."""

    def test_realistic_newsletter(self):
        html = """
        <html><body>
        <a href="https://tracking.example.com/click?id=abc123&redirect=https://example.com/article/some-long-path-that-exceeds-fifty-chars-easily-for-testing">
            <img src="https://images.example.com/newsletter/hero-banner-2024-spring-edition-1200x600.png?v=12345678&cache=bust" alt="Spring Sale" />
        </a>
        <p>Don't miss our <a href="https://tracking.example.com/click?id=def456&redirect=https://example.com/deals/spring-2024-mega-sale-event-page">spring deals</a>!</p>
        <p><strong><a href="https://tracking.example.com/click?id=ghi789&redirect=https://example.com/subscribe/newsletter-signup">Subscribe</a></strong></p>
        <p>Short link: <a href="https://example.com">here</a></p>
        <p>Bare URL: https://tracking.example.com/click?id=jkl012&redirect=https://example.com/unsubscribe/from-all-marketing-emails</p>
        </body></html>
        """

        markdown_text = convert_html_to_markdown(html)

        result, mapping = replace_urls_with_identifiers(markdown_text)

        # No URL of 50+ characters should remain in the output
        import re
        remaining_urls = re.findall(r'https?://\S{47,}', result)
        assert remaining_urls == [], f"Long URLs still present: {remaining_urls}"

        # Should have captured multiple URLs
        assert len(mapping) >= 3

        # Short link (https://example.com) should still be in the output
        assert "https://example.com" in result or "example.com" in result


# ---------------------------------------------------------------------------
# Full pipeline test: HTML -> markdown -> clean -> URL replacement
# ---------------------------------------------------------------------------

class TestFullPipelineWithCleanup:
    """Test 19: Full pipeline -- HTML with zero-width chars and excessive
    newlines goes through convert_html_to_markdown(), clean_email_markdown(),
    then replace_urls_with_identifiers(). Verifies no invisible chars, no
    triple+ newlines, and URLs replaced."""

    def test_full_pipeline(self):
        import re

        # HTML with ZWNJ padding (as used in Gmail preview text), excessive
        # <br> tags, and long tracking URLs.
        html = """
        <html><body>
        <div style="display:none;max-height:0px;overflow:hidden;">
            Preview\u200c\u200c\u200c\u200c\u200c text padding
        </div>
        <br><br><br><br>
        <h1>Welcome!</h1>
        <br><br><br>
        <p>Check out <a href="https://tracking.example.com/click?id=abc123&redirect=https://example.com/article/some-very-long-path-for-testing">this article</a>.</p>
        <br><br><br><br><br>
        <p>Or visit <a href="https://example.com">our site</a>.</p>
        <br><br><br>
        <p>Unsubscribe: https://tracking.example.com/click?id=unsub999&redirect=https://example.com/unsubscribe/from-all-emails</p>
        </body></html>
        """

        # Step 1: HTML -> markdown
        markdown_text = convert_html_to_markdown(html)

        # Step 2: Clean
        cleaned = clean_email_markdown(markdown_text)

        # Step 3: URL replacement
        result, mapping = replace_urls_with_identifiers(cleaned)

        # No invisible Unicode characters should remain
        for cp in _INVISIBLE_UNICODE_CODEPOINTS:
            assert chr(cp) not in result, f"U+{cp:04X} still in final output"

        # No runs of 3+ newlines should remain
        triple_newlines = re.findall(r'\n{3,}', result)
        assert triple_newlines == [], f"Triple+ newlines found: {triple_newlines}"

        # Long URLs should be replaced
        remaining_long = re.findall(r'https?://\S{47,}', result)
        assert remaining_long == [], f"Long URLs still present: {remaining_long}"

        # Should have captured at least 2 long URLs
        assert len(mapping) >= 2

        # Short URL (https://example.com) should still be present
        assert "https://example.com" in result or "example.com" in result

        # Visible content should be preserved
        assert "Welcome!" in result
        assert "this article" in result
