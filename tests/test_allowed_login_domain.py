"""Tests for the Google-login domain resolution.

allowed_login_domain() must honor QUEST_ALLOWED_LOGIN_DOMAIN only in local
mode, honor server_config.json's ``allowed_login_domain`` key in all modes
(written by the prod bootstrap wizard), and otherwise fall back to the
hardcoded ALLOWED_DOMAIN. The Google login callback always enforces *some*
domain, and a stray environment variable must never widen staging/prod
access beyond the configured domain.
"""

import json

import pytest

import config.server_config as server_config_module
from auth.config import ALLOWED_DOMAIN, allowed_login_domain


@pytest.fixture(autouse=True)
def isolated_server_config(tmp_path, monkeypatch):
    """Point the server-config loader at a per-test file (absent by default)."""
    path = tmp_path / "server_config.json"
    monkeypatch.setattr(server_config_module, "SERVER_CONFIG_FILE", path)
    return path


def _write_config(path, data):
    path.write_text(json.dumps(data))


class TestAllowedLoginDomain:
    def test_default_without_override(self, monkeypatch):
        monkeypatch.setenv("QUEST_ENV", "local")
        monkeypatch.delenv("QUEST_ALLOWED_LOGIN_DOMAIN", raising=False)
        assert allowed_login_domain() == ALLOWED_DOMAIN

    def test_local_mode_honors_override(self, monkeypatch):
        monkeypatch.setenv("QUEST_ENV", "local")
        monkeypatch.setenv("QUEST_ALLOWED_LOGIN_DOMAIN", "example.org")
        assert allowed_login_domain() == "example.org"

    def test_leading_at_sign_stripped(self, monkeypatch):
        monkeypatch.setenv("QUEST_ENV", "local")
        monkeypatch.setenv("QUEST_ALLOWED_LOGIN_DOMAIN", "@example.org")
        assert allowed_login_domain() == "example.org"

    def test_override_ignored_outside_local_mode(self, monkeypatch):
        monkeypatch.setenv("QUEST_ALLOWED_LOGIN_DOMAIN", "example.org")
        for env in ("staging", "prod"):
            monkeypatch.setenv("QUEST_ENV", env)
            assert allowed_login_domain() == ALLOWED_DOMAIN

    def test_empty_override_falls_back(self, monkeypatch):
        monkeypatch.setenv("QUEST_ENV", "local")
        monkeypatch.setenv("QUEST_ALLOWED_LOGIN_DOMAIN", "  ")
        assert allowed_login_domain() == ALLOWED_DOMAIN


class TestServerConfigDomain:
    def test_config_key_honored_in_prod(self, monkeypatch, isolated_server_config):
        monkeypatch.setenv("QUEST_ENV", "prod")
        monkeypatch.delenv("QUEST_ALLOWED_LOGIN_DOMAIN", raising=False)
        _write_config(isolated_server_config, {"allowed_login_domain": "otherco.com"})
        assert allowed_login_domain() == "otherco.com"

    def test_config_key_honored_in_staging_and_local(self, monkeypatch, isolated_server_config):
        monkeypatch.delenv("QUEST_ALLOWED_LOGIN_DOMAIN", raising=False)
        _write_config(isolated_server_config, {"allowed_login_domain": "otherco.com"})
        for env in ("staging", "local"):
            monkeypatch.setenv("QUEST_ENV", env)
            assert allowed_login_domain() == "otherco.com"

    def test_local_env_override_beats_config_key(self, monkeypatch, isolated_server_config):
        monkeypatch.setenv("QUEST_ENV", "local")
        monkeypatch.setenv("QUEST_ALLOWED_LOGIN_DOMAIN", "example.org")
        _write_config(isolated_server_config, {"allowed_login_domain": "otherco.com"})
        assert allowed_login_domain() == "example.org"

    def test_env_override_still_ignored_outside_local(self, monkeypatch, isolated_server_config):
        monkeypatch.setenv("QUEST_ENV", "prod")
        monkeypatch.setenv("QUEST_ALLOWED_LOGIN_DOMAIN", "example.org")
        _write_config(isolated_server_config, {"allowed_login_domain": "otherco.com"})
        assert allowed_login_domain() == "otherco.com"

    def test_config_key_at_sign_and_whitespace_stripped(self, monkeypatch, isolated_server_config):
        monkeypatch.setenv("QUEST_ENV", "prod")
        _write_config(isolated_server_config, {"allowed_login_domain": " @otherco.com "})
        assert allowed_login_domain() == "otherco.com"

    def test_empty_config_key_falls_back(self, monkeypatch, isolated_server_config):
        monkeypatch.setenv("QUEST_ENV", "prod")
        monkeypatch.delenv("QUEST_ALLOWED_LOGIN_DOMAIN", raising=False)
        _write_config(isolated_server_config, {"allowed_login_domain": ""})
        assert allowed_login_domain() == ALLOWED_DOMAIN
