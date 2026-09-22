"""Tests for run.py's local-mode dev-config.json pre-baking.

The shared parent-directory dev-config.json carries a Vertex service-account
key (materialized into the data directory and exported as
GOOGLE_APPLICATION_CREDENTIALS) and per-service credentials pre-baked into
the service_credentials store. Malformed input must never raise: a broken
shared config cannot be allowed to block local startup.
"""

import json
import os
import stat

import run


def _project_root(tmp_path):
    """A fake checkout directory whose parent holds the shared dev config."""
    root = tmp_path / "quest1"
    root.mkdir()
    return root


def _write_dev_config(tmp_path, data):
    (tmp_path / run.DEV_CONFIG_FILENAME).write_text(json.dumps(data))


def _clear_env(monkeypatch, var):
    """Unset var, restoring the pre-test state even if the code under test
    sets it (a bare delenv(raising=False) records nothing for absent vars)."""
    monkeypatch.setenv(var, "sentinel")
    monkeypatch.delenv(var)


class TestLoadDevConfig:
    def test_missing_file_returns_empty(self, tmp_path):
        assert run.load_dev_config(_project_root(tmp_path)) == {}

    def test_reads_parent_directory_file(self, tmp_path):
        root = _project_root(tmp_path)
        _write_dev_config(tmp_path, {"service_credentials": {}})
        assert run.load_dev_config(root) == {"service_credentials": {}}

    def test_malformed_json_returns_empty(self, tmp_path, capsys):
        root = _project_root(tmp_path)
        (tmp_path / run.DEV_CONFIG_FILENAME).write_text("{not json")
        assert run.load_dev_config(root) == {}
        assert "Ignoring unreadable" in capsys.readouterr().out

    def test_non_object_returns_empty(self, tmp_path, capsys):
        root = _project_root(tmp_path)
        (tmp_path / run.DEV_CONFIG_FILENAME).write_text('["a list"]')
        assert run.load_dev_config(root) == {}
        assert "expected a JSON object" in capsys.readouterr().out


class TestSetupVertexCredentials:
    def test_key_materialized_into_data_dir(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch, "GOOGLE_APPLICATION_CREDENTIALS")
        root = _project_root(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        key = {"type": "service_account", "project_id": "proj-a"}
        run.setup_vertex_credentials(root, data_dir, {"vertex_service_account": key})

        key_path = data_dir / run.LEGACY_VERTEX_CREDENTIALS_FILENAME
        assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(key_path)
        assert json.loads(key_path.read_text()) == key
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    def test_externally_set_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/elsewhere/key.json")
        root = _project_root(tmp_path)
        run.setup_vertex_credentials(
            root, tmp_path / "data", {"vertex_service_account": {"project_id": "p"}}
        )
        assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == "/elsewhere/key.json"

    def test_legacy_bare_key_file_fallback(self, tmp_path, monkeypatch, capsys):
        _clear_env(monkeypatch, "GOOGLE_APPLICATION_CREDENTIALS")
        root = _project_root(tmp_path)
        legacy = tmp_path / run.LEGACY_VERTEX_CREDENTIALS_FILENAME
        legacy.write_text(json.dumps({"project_id": "proj-a"}))
        run.setup_vertex_credentials(root, tmp_path / "data", {})
        assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(legacy)
        assert "deprecated" in capsys.readouterr().out

    def test_no_key_anywhere_is_a_no_op(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch, "GOOGLE_APPLICATION_CREDENTIALS")
        run.setup_vertex_credentials(_project_root(tmp_path), tmp_path / "data", {})
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ


class TestExportDevConfigEnv:
    def _clear_all(self, monkeypatch):
        for _, env_var, _ in run.DEV_CONFIG_ENV_EXPORTS:
            _clear_env(monkeypatch, env_var)

    def test_exports_env_vars(self, monkeypatch):
        self._clear_all(monkeypatch)
        run.export_dev_config_env(
            {"oauth_hostname": "dev.example.com", "allowed_login_domain": "example.org"}
        )
        assert os.environ["QUEST_OAUTH_HOSTNAME"] == "dev.example.com"
        assert os.environ["QUEST_ALLOWED_LOGIN_DOMAIN"] == "example.org"

    def test_externally_set_env_wins(self, monkeypatch):
        self._clear_all(monkeypatch)
        monkeypatch.setenv("QUEST_OAUTH_HOSTNAME", "other.host")
        run.export_dev_config_env(
            {"oauth_hostname": "dev.example.com", "allowed_login_domain": "example.org"}
        )
        assert os.environ["QUEST_OAUTH_HOSTNAME"] == "other.host"
        assert os.environ["QUEST_ALLOWED_LOGIN_DOMAIN"] == "example.org"

    def test_missing_or_malformed_is_a_no_op(self, monkeypatch):
        self._clear_all(monkeypatch)
        run.export_dev_config_env({})
        run.export_dev_config_env({"oauth_hostname": ""})
        run.export_dev_config_env({"oauth_hostname": ["not", "a", "string"]})
        assert "QUEST_OAUTH_HOSTNAME" not in os.environ
        assert "QUEST_ALLOWED_LOGIN_DOMAIN" not in os.environ


class TestPrebakeServiceCredentials:
    def test_bakes_google_oauth_into_store(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        creds = {"web": {"client_id": "id", "client_secret": "secret"}}
        baked = run.prebake_service_credentials(
            data_dir, {"service_credentials": {"google_oauth": creds}}
        )
        assert baked == ["google_oauth"]
        target = data_dir / "service_credentials" / "google_oauth.json"
        assert json.loads(target.read_text()) == creds
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700

    def test_existing_store_file_wins(self, tmp_path):
        data_dir = tmp_path / "data"
        store = data_dir / "service_credentials"
        store.mkdir(parents=True)
        existing = {"web": {"client_id": "admin-edited"}}
        (store / "google_oauth.json").write_text(json.dumps(existing))
        baked = run.prebake_service_credentials(
            data_dir,
            {"service_credentials": {"google_oauth": {"web": {"client_id": "new"}}}},
        )
        assert baked == []
        assert json.loads((store / "google_oauth.json").read_text()) == existing

    def test_skips_malformed_entries(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        baked = run.prebake_service_credentials(
            data_dir,
            {
                "service_credentials": {
                    "../evil": {"client_id": "x"},
                    "Bad Name": {"client_id": "x"},
                    "empty": {},
                    "not_a_dict": "creds",
                    "slack": {"client_id": "ok"},
                }
            },
        )
        assert baked == ["slack"]
        store = data_dir / "service_credentials"
        assert sorted(p.name for p in store.iterdir()) == ["slack.json"]
        assert capsys.readouterr().out.count("skipping malformed") == 4

    def test_missing_or_malformed_section_is_a_no_op(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        assert run.prebake_service_credentials(data_dir, {}) == []
        assert run.prebake_service_credentials(
            data_dir, {"service_credentials": "nope"}
        ) == []
        assert not (data_dir / "service_credentials").exists()
