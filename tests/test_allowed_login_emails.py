"""Tests for the individual-email sign-in whitelist.

is_login_allowed() must admit emails on the allowed domain (the historical
behavior) OR emails listed in server_config.json's ``allowed_login_emails``
key, case-insensitively. With no whitelist configured it must behave exactly
like the old domain-only check, and the whitelist must never be enumerated
in the unauthenticated login_restriction_description().
"""

import json

import pytest

import config.server_config as server_config_module
from auth.config import (
    ALLOWED_DOMAIN,
    allowed_login_emails,
    is_login_allowed,
    login_restriction_description,
)


@pytest.fixture(autouse=True)
def isolated_server_config(tmp_path, monkeypatch):
    """Point the server-config loader at a per-test file (absent by default)."""
    path = tmp_path / "server_config.json"
    monkeypatch.setattr(server_config_module, "SERVER_CONFIG_FILE", path)
    monkeypatch.setenv("QUEST_ENV", "prod")
    monkeypatch.delenv("QUEST_ALLOWED_LOGIN_DOMAIN", raising=False)
    return path


def _write_config(path, data):
    path.write_text(json.dumps(data))


class TestAllowedLoginEmails:
    def test_unconfigured_is_empty(self):
        assert allowed_login_emails() == []

    def test_normalizes_case_and_whitespace(self, isolated_server_config):
        _write_config(
            isolated_server_config,
            {"allowed_login_emails": ["  Kid@Gmail.com ", "", "mom@gmail.com"]},
        )
        assert allowed_login_emails() == ["kid@gmail.com", "mom@gmail.com"]

    def test_non_list_value_is_ignored(self, isolated_server_config):
        _write_config(
            isolated_server_config, {"allowed_login_emails": "kid@gmail.com"}
        )
        assert allowed_login_emails() == []


class TestIsLoginAllowed:
    def test_domain_check_without_whitelist(self):
        assert is_login_allowed(f"someone@{ALLOWED_DOMAIN}")
        assert not is_login_allowed("someone@gmail.com")

    def test_whitelisted_email_admitted(self, isolated_server_config):
        _write_config(
            isolated_server_config,
            {"allowed_login_emails": ["kid@gmail.com"]},
        )
        assert is_login_allowed("kid@gmail.com")

    def test_whitelist_is_case_insensitive(self, isolated_server_config):
        _write_config(
            isolated_server_config,
            {"allowed_login_emails": ["Kid@Gmail.com"]},
        )
        assert is_login_allowed("kid@gmail.com")
        assert is_login_allowed("KID@GMAIL.COM")

    def test_unlisted_same_provider_email_rejected(self, isolated_server_config):
        _write_config(
            isolated_server_config,
            {"allowed_login_emails": ["kid@gmail.com"]},
        )
        assert not is_login_allowed("stranger@gmail.com")

    def test_domain_still_admitted_alongside_whitelist(self, isolated_server_config):
        _write_config(
            isolated_server_config,
            {
                "allowed_login_domain": "otherco.com",
                "allowed_login_emails": ["kid@gmail.com"],
            },
        )
        assert is_login_allowed("employee@otherco.com")
        assert is_login_allowed("kid@gmail.com")
        assert not is_login_allowed(f"someone@{ALLOWED_DOMAIN}")

    def test_empty_email_rejected(self):
        assert not is_login_allowed("")
        assert not is_login_allowed(None)


class TestLoginRestrictionDescription:
    def test_domain_only(self):
        assert login_restriction_description() == f"@{ALLOWED_DOMAIN} accounts"

    def test_whitelist_never_enumerated(self, isolated_server_config):
        _write_config(
            isolated_server_config,
            {"allowed_login_emails": ["kid@gmail.com"]},
        )
        description = login_restriction_description()
        assert description == "approved accounts"
        assert "kid@gmail.com" not in description
