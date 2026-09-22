"""Repo-root pytest hooks: encryption key for the test session, plus
collection of out-of-tree plugin test suites.

Encryption at rest (config/encryption.py) needs a password and a key file
before any ORM write of a secret column or credential-store file. The
autouse session fixture below points ``QUEST_ENCRYPTION_KEY_FILE`` at a
throwaway temp file and sets a test password so the whole suite runs
without touching the real data directory, and so a test that switches
``QUEST_ENV`` to prod (where the local sentinel is refused) still has a
valid password.

Plugin-specific tests live in each plugin's own ``tests/`` directory
(``plugins/<name>/tests`` in tree; ``testpaths`` in pyproject.toml
collects those). When ``QUEST_PLUGIN_PATH`` names extra plugin roots
(the same env var the loader scans -- see config/plugins.py), a bare
``uv run pytest`` also collects ``<root>/<name>/tests`` for every
plugin directory there that has one. Explicit path arguments disable
the auto-append so ``uv run pytest tests/test_foo.py`` runs exactly
what was asked.

Out-of-tree suites are collected under the module name
``<name>.tests.test_*``, so the plugin directory and its ``tests/``
directory each need an ``__init__.py`` (matching the in-tree layout);
see tests/plugin_support.py for the fixture recipe.
"""


import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _test_encryption_key():
    tmpdir = tempfile.mkdtemp(prefix="quest_test_enc_")
    previous = {
        name: os.environ.get(name)
        for name in ("QUEST_ENCRYPTION_KEY_FILE", "QUEST_ENCRYPTION_PASSWORD", "QUEST_ENCRYPTION_PASSWORD_FILE")
    }
    os.environ["QUEST_ENCRYPTION_KEY_FILE"] = os.path.join(tmpdir, "encryption_key.json")
    os.environ["QUEST_ENCRYPTION_PASSWORD"] = "quest-test-suite-password"
    os.environ.pop("QUEST_ENCRYPTION_PASSWORD_FILE", None)
    from config import encryption
    encryption.reset_cache()
    encryption.load_data_key(create=True)
    yield
    encryption.reset_cache()
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def pytest_configure(config):
    if config.option.file_or_dir:
        return
    from config.plugins import _extra_plugin_roots

    for root in _extra_plugin_roots():
        for child in sorted(root.iterdir()):
            if child.name.startswith(("_", ".")) or not child.is_dir():
                continue
            tests_dir = child / "tests"
            if (child / "plugin.py").is_file() and tests_dir.is_dir():
                config.args.append(str(tests_dir))
