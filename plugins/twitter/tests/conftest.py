"""Fixtures for the Twitter/X plugin's test suite (see tests/plugin_support.py)."""

from pathlib import Path

from tests.plugin_support import plugin_fixture

twitter_plugin = plugin_fixture(Path(__file__).resolve().parent.parent)
