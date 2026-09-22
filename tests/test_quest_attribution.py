"""Tests for the app_base_url public-URL config read.

Outgoing messages attribute themselves to Quest with a link to the
deployment's public URL (the ``app_base_url`` server config key, read via
get_app_base_url()). With no URL configured the attribution degrades to
plain "Quest" text -- an open-source checkout must not link to any
particular deployment. The Slack attribution-block tests moved to the
Slack plugin's suite (plugins/slack/tests/test_slack_handlers.py).
"""

import config.server_config as server_config
from config.server_config import get_app_base_url


def _set_config(monkeypatch, **config):
    monkeypatch.setattr(server_config, "load_server_config", lambda: config)


def test_get_app_base_url_unset_returns_empty(monkeypatch):
    _set_config(monkeypatch)
    assert get_app_base_url() == ""


def test_get_app_base_url_strips_trailing_slash(monkeypatch):
    _set_config(monkeypatch, app_base_url="https://quest.example.com/")
    assert get_app_base_url() == "https://quest.example.com"


