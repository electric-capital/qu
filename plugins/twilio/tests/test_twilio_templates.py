"""Tests for the pre-written SMS message helpers (plugins/twilio/templates.py)."""

import pytest

from plugins.twilio.templates import (
    MAX_TEMPLATES,
    TEMPLATES_SETTINGS_KEY,
    find_template,
    get_configured_templates,
    validate_templates,
)
from plugins.twilio.upstream import MAX_SMS_BODY_LENGTH


class TestValidate:
    def test_normalizes_rows(self):
        out = validate_templates([
            {"name": "  Deploy done ", "body": "  Deploy finished.\nAll good. "},
        ])
        assert out == [{"name": "Deploy done", "body": "Deploy finished.\nAll good."}]

    def test_empty_list_allowed(self):
        assert validate_templates([]) == []

    @pytest.mark.parametrize("bad,match", [
        ("nope", "must be a list"),
        ([1], "must be an object"),
        ([{"name": "", "body": "x"}], "needs a name"),
        ([{"name": "a", "body": "   "}], "needs a body"),
        ([{"name": "a"}], "needs a body"),
        ([{"name": "a", "body": "x", "extra": 1}], "unknown fields"),
        ([{"name": "a" * 61, "body": "x"}], "too long"),
        ([{"name": "a", "body": "x" * (MAX_SMS_BODY_LENGTH + 1)}], "too long"),
        ([{"name": "a\x00", "body": "x"}], "control characters"),
        ([{"name": "a", "body": "x\x07"}], "control characters"),
        ([{"name": "A", "body": "x"}, {"name": "a", "body": "y"}], "Duplicate"),
        ([{"name": str(i), "body": "x"} for i in range(MAX_TEMPLATES + 1)], "At most"),
    ])
    def test_rejections(self, bad, match):
        with pytest.raises(ValueError, match=match):
            validate_templates(bad)

    def test_newlines_and_tabs_allowed_in_body(self):
        out = validate_templates([{"name": "a", "body": "line1\nline2\tx"}])
        assert out[0]["body"] == "line1\nline2\tx"


class TestRead:
    def test_reads_settings_key(self):
        settings = {TEMPLATES_SETTINGS_KEY: [{"name": "a", "body": "x"}]}
        assert get_configured_templates(settings) == [{"name": "a", "body": "x"}]

    def test_tolerates_garbage(self):
        assert get_configured_templates(None) == []
        assert get_configured_templates({}) == []
        assert get_configured_templates({TEMPLATES_SETTINGS_KEY: "nope"}) == []
        assert get_configured_templates({TEMPLATES_SETTINGS_KEY: [
            "str", {"name": "", "body": "x"}, {"name": "ok", "body": "y"}, {"name": "n"},
        ]}) == [{"name": "ok", "body": "y"}]

    def test_find_is_case_insensitive(self):
        templates = [{"name": "Deploy done", "body": "x"}]
        assert find_template(templates, " deploy DONE ") == templates[0]
        assert find_template(templates, "other") is None
