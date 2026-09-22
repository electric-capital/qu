from pathlib import Path

from tests.plugin_support import plugin_fixture

slack_plugin = plugin_fixture(Path(__file__).resolve().parent.parent)
