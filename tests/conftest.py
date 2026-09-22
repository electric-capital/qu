"""Shared fixtures: registering the in-tree reference plugins.

Plugin-specific test suites live in each plugin's own ``tests/``
directory (``plugins/<name>/tests``) with their own conftest. The
fixtures here serve the cross-cutting core tests that exercise
plugin-registered surfaces (connector rows, dispatch tables, the
action-request schema, system-skill enumeration, ...) with a real
plugin registered. All of them are built by the shared factory in
tests/plugin_support.py.
"""

from config.plugins import PLUGINS_DIR

from tests.plugin_support import plugin_fixture

# The _example smoke plugin (never discovered in production -- the loader
# skips _-prefixed directories, but plugin_fixture imports it directly) is
# the stand-in whenever a cross-cutting test just needs SOME registered
# plugin; plugin-specific behavior is pinned in each plugin's own tests/
# suite.
example_plugin = plugin_fixture(PLUGINS_DIR / "_example")
github_plugin = plugin_fixture(PLUGINS_DIR / "github")
slack_plugin = plugin_fixture(PLUGINS_DIR / "slack")
twitter_plugin = plugin_fixture(PLUGINS_DIR / "twitter")
telegram_plugin = plugin_fixture(PLUGINS_DIR / "telegram")
