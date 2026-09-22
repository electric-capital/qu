"""Tests for api/gmail/quest_labels.py -- Quest-managed Gmail label config."""

import pytest

from api.gmail.quest_labels import (
    MAX_GMAIL_LABELS,
    MAX_GMAIL_LABEL_NAME_LENGTH,
    full_quest_label_name,
    get_configured_gmail_labels,
    validate_gmail_label_names,
)


class TestFullQuestLabelName:
    def test_prefixes_with_quest_parent(self):
        assert full_quest_label_name("receipts") == "[Quest]/receipts"


class TestValidateGmailLabelNames:
    def test_valid_list_passes_through(self):
        assert validate_gmail_label_names(["receipts", "follow-up"]) == ["receipts", "follow-up"]

    def test_empty_list_is_valid(self):
        assert validate_gmail_label_names([]) == []

    def test_strips_surrounding_whitespace(self):
        assert validate_gmail_label_names(["  receipts  "]) == ["receipts"]

    def test_dedupes_case_insensitively_keeping_first(self):
        assert validate_gmail_label_names(["Receipts", "receipts", "RECEIPTS"]) == ["Receipts"]

    def test_preserves_original_casing(self):
        assert validate_gmail_label_names(["Follow-Up"]) == ["Follow-Up"]

    def test_rejects_non_list(self):
        with pytest.raises(ValueError, match="must be a list"):
            validate_gmail_label_names("receipts")

    def test_rejects_non_string_item(self):
        with pytest.raises(ValueError, match="must be a string"):
            validate_gmail_label_names(["receipts", 42])

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_gmail_label_names(["receipts", "   "])

    def test_rejects_slash(self):
        with pytest.raises(ValueError, match="cannot contain '/'"):
            validate_gmail_label_names(["a/b"])

    def test_rejects_control_characters(self):
        with pytest.raises(ValueError, match="control characters"):
            validate_gmail_label_names(["bad\nname"])

    def test_rejects_too_long_name(self):
        with pytest.raises(ValueError, match="too long"):
            validate_gmail_label_names(["x" * (MAX_GMAIL_LABEL_NAME_LENGTH + 1)])

    def test_accepts_max_length_name(self):
        name = "x" * MAX_GMAIL_LABEL_NAME_LENGTH
        assert validate_gmail_label_names([name]) == [name]

    def test_rejects_too_many_labels(self):
        labels = [f"label-{i}" for i in range(MAX_GMAIL_LABELS + 1)]
        with pytest.raises(ValueError, match="At most"):
            validate_gmail_label_names(labels)

    def test_accepts_max_count(self):
        labels = [f"label-{i}" for i in range(MAX_GMAIL_LABELS)]
        assert validate_gmail_label_names(labels) == labels


class TestGetConfiguredGmailLabels:
    def test_reads_from_settings(self):
        assert get_configured_gmail_labels({"gmail_labels": ["a", "b"]}) == ["a", "b"]

    def test_missing_key_returns_empty(self):
        assert get_configured_gmail_labels({}) == []

    def test_none_settings_returns_empty(self):
        assert get_configured_gmail_labels(None) == []

    def test_malformed_value_returns_empty(self):
        assert get_configured_gmail_labels({"gmail_labels": "not-a-list"}) == []

    def test_filters_non_string_and_empty_entries(self):
        assert get_configured_gmail_labels({"gmail_labels": ["a", 3, "", None, "b"]}) == ["a", "b"]
