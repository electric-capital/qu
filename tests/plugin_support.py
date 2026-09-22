"""Shared pytest support for plugin test suites.

Plugin-specific tests live in each plugin's own ``tests/`` directory:
``plugins/<name>/tests`` in tree, and ``<root>/<name>/tests`` for
out-of-tree plugins under a ``QUEST_PLUGIN_PATH`` root (collected by
the repo-root conftest.py). A suite registers the plugin(s) it
exercises into the live registries with the fixture factory below
instead of relying on server startup, e.g. in
``plugins/<name>/tests/conftest.py``::

    from pathlib import Path

    from tests.plugin_support import plugin_fixture

    my_plugin = plugin_fixture(Path(__file__).resolve().parent.parent)

Out-of-tree suites use the exact same recipe; the plugin's tests
package must be importable under a unique name, so both the plugin
directory and its ``tests/`` directory need an ``__init__.py``.
"""

from pathlib import Path

import pytest

from config import plugins as plugins_mod


def plugin_fixture(plugin_dir: Path):
    """Return a fixture that registers the plugin at ``plugin_dir``.

    The fixture imports ``<plugin_dir>/plugin.py`` the same way the
    loader does, registers the manifest into the core registries (and
    ``_LOADED``, so loaded-plugin gating sees it), yields the plugin,
    and unregisters it on teardown.
    """
    plugin_py = Path(plugin_dir).resolve() / "plugin.py"

    @pytest.fixture()
    def _plugin():
        module = plugins_mod._import_plugin_module(plugin_py)
        plugin = module.get_plugin()
        plugins_mod.register_plugin(plugin)
        plugins_mod._LOADED.append(plugin)
        yield plugin
        plugins_mod.unregister_plugin(plugin)

    return _plugin
