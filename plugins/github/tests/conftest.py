"""Fixtures for the GitHub plugin's test suite (see tests/plugin_support.py)."""

from pathlib import Path

from config.plugins import PLUGINS_DIR
from tests.plugin_support import plugin_fixture

github_plugin = plugin_fixture(Path(__file__).resolve().parent.parent)

# The oauth-router mount tests contrast against an api_key-kind plugin;
# the _example smoke plugin is the stand-in (kind="api_key").
example_plugin = plugin_fixture(PLUGINS_DIR / "_example")
