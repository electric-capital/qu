"""Tests for oauth_base_url() in auth/config.py.

The helper builds the base URL for OAuth redirect/callback URIs. The full
``app_base_url`` server config override, when set, is used verbatim;
otherwise the optional ``oauth_hostname`` override replaces the request's
hostname (providers like Google reject raw-IP redirect URIs) while
preserving scheme and port.
"""

from types import SimpleNamespace

import config.server_config as server_config
from auth.config import oauth_base_url


def _request(base_url: str):
    return SimpleNamespace(base_url=base_url)


def _set_config(monkeypatch, **config):
    monkeypatch.setattr(server_config, "load_server_config", lambda: config)


def _set_hostname(monkeypatch, hostname: str):
    _set_config(monkeypatch, oauth_hostname=hostname)


def test_no_override_returns_request_base_url(monkeypatch):
    _set_hostname(monkeypatch, "")
    assert oauth_base_url(_request("http://10.1.2.3:9000/")) == "http://10.1.2.3:9000"


def test_override_replaces_hostname_keeps_port(monkeypatch):
    _set_hostname(monkeypatch, "dev.example.com")
    assert (
        oauth_base_url(_request("http://10.1.2.3:9000/"))
        == "http://dev.example.com:9000"
    )


def test_override_replaces_hostname_without_port(monkeypatch):
    _set_hostname(monkeypatch, "dev.example.com")
    assert (
        oauth_base_url(_request("https://example.internal/"))
        == "https://dev.example.com"
    )


def test_override_from_config_file(tmp_path, monkeypatch):
    config_file = tmp_path / "server_config.json"
    config_file.write_text('{"oauth_hostname": "dev.example.com"}')
    monkeypatch.setattr(server_config, "SERVER_CONFIG_FILE", config_file)
    assert (
        oauth_base_url(_request("http://10.1.2.3:9000/"))
        == "http://dev.example.com:9000"
    )


def test_env_var_overrides_config_file(tmp_path, monkeypatch):
    config_file = tmp_path / "server_config.json"
    config_file.write_text('{"oauth_hostname": "from-file.example"}')
    monkeypatch.setattr(server_config, "SERVER_CONFIG_FILE", config_file)
    monkeypatch.setenv("QUEST_OAUTH_HOSTNAME", "from-env.example")
    assert (
        oauth_base_url(_request("http://10.1.2.3:9000/"))
        == "http://from-env.example:9000"
    )


def test_app_base_url_used_verbatim(monkeypatch):
    _set_config(monkeypatch, app_base_url="https://quest.example.com")
    assert (
        oauth_base_url(_request("http://10.1.2.3:9000/"))
        == "https://quest.example.com"
    )


def test_app_base_url_trailing_slash_stripped(monkeypatch):
    _set_config(monkeypatch, app_base_url="https://quest.example.com/")
    assert (
        oauth_base_url(_request("http://10.1.2.3:9000/"))
        == "https://quest.example.com"
    )


def test_app_base_url_wins_over_oauth_hostname(monkeypatch):
    _set_config(
        monkeypatch,
        app_base_url="https://quest.example.com",
        oauth_hostname="dev.example.com",
    )
    assert (
        oauth_base_url(_request("http://10.1.2.3:9000/"))
        == "https://quest.example.com"
    )


def test_app_base_url_env_var(tmp_path, monkeypatch):
    config_file = tmp_path / "server_config.json"
    config_file.write_text('{"oauth_hostname": "from-file.example"}')
    monkeypatch.setattr(server_config, "SERVER_CONFIG_FILE", config_file)
    monkeypatch.setenv("QUEST_APP_BASE_URL", "https://env.example.com")
    assert (
        oauth_base_url(_request("http://10.1.2.3:9000/"))
        == "https://env.example.com"
    )
