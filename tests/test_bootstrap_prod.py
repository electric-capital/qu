"""Tests for the prod first-run bootstrap wizard (scripts/bootstrap_prod.py).

The wizard gates startup on the config a usable prod instance cannot run
without (admin_emails + Google OAuth), prompts interactively, and writes
server_config.json, the google_oauth service-credential store file, and the
Vertex service-account key copy. Non-interactive unconfigured startups must
abort with instructions instead of launching a server nobody can log into.
"""

import json
import os
import stat

import pytest

import run
from scripts import bootstrap_prod


@pytest.fixture
def deployment(tmp_path):
    """A fake project root + data dir for a fresh deployment."""
    project_root = tmp_path / "quest"
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True)
    return project_root, data_dir


def _answers(monkeypatch, values):
    """Feed a fixed sequence of answers to input() and getpass()."""
    it = iter(values)

    def _next(prompt=""):
        return next(it)

    monkeypatch.setattr("builtins.input", _next)
    monkeypatch.setattr("getpass.getpass", _next)
    return it


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class TestMissingRequiredConfig:
    def test_fresh_deployment_missing_both(self, deployment):
        project_root, data_dir = deployment
        missing = bootstrap_prod.missing_required_config(project_root, data_dir)
        assert len(missing) == 2
        assert any("admin_emails" in m for m in missing)
        assert any("Google OAuth" in m for m in missing)

    def test_admin_emails_only(self, deployment):
        project_root, data_dir = deployment
        (project_root / "server_config.json").write_text(
            json.dumps({"admin_emails": ["ops@otherco.com"]})
        )
        missing = bootstrap_prod.missing_required_config(project_root, data_dir)
        assert len(missing) == 1
        assert "Google OAuth" in missing[0]

    def test_store_google_oauth_counts(self, deployment):
        project_root, data_dir = deployment
        (project_root / "server_config.json").write_text(
            json.dumps({"admin_emails": ["ops@otherco.com"]})
        )
        store = data_dir / "service_credentials"
        store.mkdir()
        (store / "google_oauth.json").write_text(json.dumps({"web": {"client_id": "x"}}))
        assert bootstrap_prod.missing_required_config(project_root, data_dir) == []

    def test_legacy_google_oauth_counts(self, deployment):
        project_root, data_dir = deployment
        (project_root / "server_config.json").write_text(
            json.dumps({"admin_emails": ["ops@otherco.com"]})
        )
        (project_root / "server_credentials.json").write_text(
            json.dumps({"google_oauth": {"web": {"client_id": "x"}}})
        )
        assert bootstrap_prod.missing_required_config(project_root, data_dir) == []

    def test_malformed_config_treated_as_missing(self, deployment):
        project_root, data_dir = deployment
        (project_root / "server_config.json").write_text("not json{")
        missing = bootstrap_prod.missing_required_config(project_root, data_dir)
        assert len(missing) == 2


class TestLlmConfigured:
    def test_unconfigured(self, deployment, monkeypatch):
        project_root, _ = deployment
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT_ID", raising=False)
        assert not bootstrap_prod.llm_configured(project_root)

    def test_vertex_project_counts(self, deployment, monkeypatch):
        project_root, _ = deployment
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT_ID", raising=False)
        (project_root / "server_config.json").write_text(
            json.dumps({"anthropic": {"vertex_project_id": "proj"}})
        )
        assert bootstrap_prod.llm_configured(project_root)

    def test_env_var_counts(self, deployment, monkeypatch):
        project_root, _ = deployment
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
        assert bootstrap_prod.llm_configured(project_root)

    def test_gemini_api_key_no_longer_counts(self, deployment, monkeypatch):
        """The genapi transport was removed: a legacy Gemini API key in
        server_credentials.json no longer configures any model."""
        project_root, _ = deployment
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT_ID", raising=False)
        (project_root / "server_credentials.json").write_text(
            json.dumps({"gemini": {"api_key": "k"}})
        )
        assert not bootstrap_prod.llm_configured(project_root)


class TestNonInteractive:
    """pytest replaces stdin with a non-tty object, exercising the abort path."""

    def test_unconfigured_aborts(self, deployment, monkeypatch, capsys):
        project_root, data_dir = deployment
        monkeypatch.delenv(bootstrap_prod.SKIP_BOOTSTRAP_ENV, raising=False)
        with pytest.raises(SystemExit) as exc:
            bootstrap_prod.maybe_run_bootstrap(project_root, data_dir)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "not configured" in out
        assert "QUEST_SKIP_BOOTSTRAP" in out

    def test_skip_env_continues(self, deployment, monkeypatch, capsys):
        project_root, data_dir = deployment
        monkeypatch.setenv(bootstrap_prod.SKIP_BOOTSTRAP_ENV, "1")
        bootstrap_prod.maybe_run_bootstrap(project_root, data_dir)
        assert "QUEST_SKIP_BOOTSTRAP set" in capsys.readouterr().out

    def test_configured_deployment_skips_silently(self, deployment, monkeypatch, capsys):
        project_root, data_dir = deployment
        monkeypatch.delenv(bootstrap_prod.SKIP_BOOTSTRAP_ENV, raising=False)
        (project_root / "server_config.json").write_text(
            json.dumps({"admin_emails": ["ops@otherco.com"]})
        )
        store = data_dir / "service_credentials"
        store.mkdir()
        (store / "google_oauth.json").write_text(json.dumps({"web": {"client_id": "x"}}))
        bootstrap_prod.maybe_run_bootstrap(project_root, data_dir)
        assert capsys.readouterr().out == ""


class TestWizard:
    def test_full_run_writes_everything(self, deployment, monkeypatch, tmp_path):
        project_root, data_dir = deployment
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT_ID", raising=False)
        # Pre-existing unrelated config must survive the merge.
        (project_root / "server_config.json").write_text(
            json.dumps({"custom_section": {"api_base": "https://internal.otherco.com"}})
        )
        key_file = tmp_path / "sa-key.json"
        key_file.write_text(
            json.dumps({"type": "service_account", "project_id": "otherco-quest"})
        )
        _answers(monkeypatch, [
            "ops@otherco.com, eng@otherco.com",  # admin emails
            "",                                   # login domain -> default otherco.com
            "kid@gmail.com, mom@gmail.com",       # additional allowed emails
            "quest.otherco.com",                  # public URL (scheme defaulted)
            "cid.apps.googleusercontent.com",     # oauth client id
            "sekrit",                             # oauth client secret (getpass)
            "",                                   # oauth project id (skip)
            str(key_file),                        # vertex key path
            "",                                   # vertex project -> default from key
        ])

        bootstrap_prod.run_wizard(project_root, data_dir)

        config = json.loads((project_root / "server_config.json").read_text())
        assert config["admin_emails"] == ["ops@otherco.com", "eng@otherco.com"]
        assert config["allowed_login_domain"] == "otherco.com"
        assert config["allowed_login_emails"] == ["kid@gmail.com", "mom@gmail.com"]
        assert config["app_base_url"] == "https://quest.otherco.com"
        assert config["anthropic"]["vertex_project_id"] == "otherco-quest"
        assert config["custom_section"]["api_base"] == "https://internal.otherco.com"

        oauth_file = data_dir / "service_credentials" / "google_oauth.json"
        assert _mode(oauth_file) == 0o600
        web = json.loads(oauth_file.read_text())["web"]
        assert web["client_id"] == "cid.apps.googleusercontent.com"
        assert web["client_secret"] == "sekrit"
        assert web["auth_uri"] == bootstrap_prod.GOOGLE_OAUTH_WEB_DEFAULTS["auth_uri"]
        assert web["redirect_uris"] == [
            "https://quest.otherco.com/auth/callback",
            "https://quest.otherco.com/auth/google-services/callback",
        ]

        key_copy = data_dir / bootstrap_prod.VERTEX_KEY_FILENAME
        assert _mode(key_copy) == 0o600
        assert json.loads(key_copy.read_text())["project_id"] == "otherco-quest"

        assert bootstrap_prod.missing_required_config(project_root, data_dir) == []

    def test_skipping_google_oauth_leaves_it_missing(self, deployment, monkeypatch):
        project_root, data_dir = deployment
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT_ID", raising=False)
        _answers(monkeypatch, [
            "ops@otherco.com",  # admin emails
            "",                  # login domain -> default
            "",                  # additional allowed emails (skip)
            "",                  # public URL (skip)
            "",                  # oauth client id (skip section)
            "",                  # vertex key path (skip)
            "",                  # vertex project (skip)
        ])

        bootstrap_prod.run_wizard(project_root, data_dir)

        config = json.loads((project_root / "server_config.json").read_text())
        assert config["admin_emails"] == ["ops@otherco.com"]
        assert not (data_dir / "service_credentials" / "google_oauth.json").exists()
        missing = bootstrap_prod.missing_required_config(project_root, data_dir)
        assert len(missing) == 1 and "Google OAuth" in missing[0]

    def test_invalid_admin_email_reprompts(self, deployment, monkeypatch):
        project_root, data_dir = deployment
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT_ID", raising=False)
        _answers(monkeypatch, [
            "not-an-email",      # rejected
            "ops@otherco.com",   # accepted
            "", "", "", "", "", "",
        ])
        bootstrap_prod.run_wizard(project_root, data_dir)
        config = json.loads((project_root / "server_config.json").read_text())
        assert config["admin_emails"] == ["ops@otherco.com"]

    def test_skipping_vertex_leaves_llm_unconfigured(self, deployment, monkeypatch):
        """There is no Gemini-API-key fallback anymore: skipping the Vertex
        prompts leaves the deployment with no LLM credentials."""
        project_root, data_dir = deployment
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT_ID", raising=False)
        _answers(monkeypatch, [
            "ops@otherco.com",
            "", "", "", "",      # domain default, no extra emails, no public URL, skip oauth
            "",                  # vertex key path (skip)
            "",                  # vertex project (skip)
        ])
        bootstrap_prod.run_wizard(project_root, data_dir)
        assert not bootstrap_prod.llm_configured(project_root)


class TestServerVertexCredentials:
    def test_exports_key_when_present(self, deployment, monkeypatch):
        _, data_dir = deployment
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        key_path = data_dir / run.LEGACY_VERTEX_CREDENTIALS_FILENAME
        key_path.write_text(json.dumps({"type": "service_account"}))
        run.setup_server_vertex_credentials(data_dir)
        assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(key_path)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    def test_external_env_wins(self, deployment, monkeypatch):
        _, data_dir = deployment
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/elsewhere/key.json")
        key_path = data_dir / run.LEGACY_VERTEX_CREDENTIALS_FILENAME
        key_path.write_text(json.dumps({"type": "service_account"}))
        run.setup_server_vertex_credentials(data_dir)
        assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == "/elsewhere/key.json"

    def test_no_key_no_export(self, deployment, monkeypatch):
        _, data_dir = deployment
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        run.setup_server_vertex_credentials(data_dir)
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
