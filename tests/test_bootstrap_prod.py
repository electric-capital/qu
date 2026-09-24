"""Tests for the prod first-run bootstrap wizard (scripts/bootstrap_prod.py).

The wizard gates startup on the config a usable prod instance cannot run
without (admin_emails + a way to sign in: an admin password for password
sign-in, Google OAuth for Google sign-in), prompts interactively, and writes
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
            "google",                             # sign-in method
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
            "google",            # sign-in method
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
            "google",            # sign-in method
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
            "google",
            "ops@otherco.com",
            "", "", "", "",      # domain default, no extra emails, no public URL, skip oauth
            "",                  # vertex key path (skip)
            "",                  # vertex project (skip)
        ])
        bootstrap_prod.run_wizard(project_root, data_dir)
        assert not bootstrap_prod.llm_configured(project_root)


class TestPasswordSignIn:
    def _config(self, project_root, **extra):
        (project_root / "server_config.json").write_text(json.dumps({
            "admin_emails": ["ops@otherco.com"], "login_method": "password", **extra,
        }))

    def test_password_mode_needs_admin_password(self, deployment):
        project_root, data_dir = deployment
        self._config(project_root)
        missing = bootstrap_prod.missing_required_config(project_root, data_dir)
        assert len(missing) == 1 and "password for an admin" in missing[0]

    def test_pending_admin_password_counts(self, deployment):
        project_root, data_dir = deployment
        self._config(project_root)
        from config.password_hashing import write_pending_admin_passwords
        write_pending_admin_passwords(data_dir, {"OPS@otherco.com": "scrypt$1$1$1$x$y"})
        assert bootstrap_prod.missing_required_config(project_root, data_dir) == []

    def test_database_password_counts(self, deployment):
        import sqlite3
        project_root, data_dir = deployment
        self._config(project_root)
        conn = sqlite3.connect(data_dir / "quest.db")
        conn.execute("CREATE TABLE users (email TEXT, password_hash TEXT)")
        conn.execute("INSERT INTO users VALUES ('ops@otherco.com', 'scrypt$...')")
        conn.commit()
        conn.close()
        assert bootstrap_prod.missing_required_config(project_root, data_dir) == []

    def test_database_without_column_is_tolerated(self, deployment):
        import sqlite3
        project_root, data_dir = deployment
        self._config(project_root)
        conn = sqlite3.connect(data_dir / "quest.db")
        conn.execute("CREATE TABLE users (email TEXT)")
        conn.close()
        assert len(bootstrap_prod.missing_required_config(project_root, data_dir)) == 1

    def test_password_mode_does_not_need_google_oauth(self, deployment):
        project_root, data_dir = deployment
        self._config(project_root)
        from config.password_hashing import write_pending_admin_passwords
        write_pending_admin_passwords(data_dir, {"ops@otherco.com": "scrypt$1$1$1$x$y"})
        assert not bootstrap_prod.google_oauth_configured(project_root, data_dir)
        assert bootstrap_prod.missing_required_config(project_root, data_dir) == []

    def test_default_wizard_run_sets_up_password_sign_in(self, deployment, monkeypatch):
        from config.password_hashing import read_pending_admin_passwords, verify_password
        project_root, data_dir = deployment
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT_ID", raising=False)
        _answers(monkeypatch, [
            "",                    # sign-in method -> default password
            "me@gmail.com",        # admin emails
            "",                    # domain: gmail.com is never suggested -> none
            "friend@gmail.com",    # additional allowed emails
            "",                    # public URL (skip)
            "short",               # admin password: too short, re-prompted
            "correct horse battery",
            "mismatch",            # repeat does not match -> re-prompted
            "correct horse battery",
            "correct horse battery",
            "",                    # vertex key path (skip)
            "",                    # vertex project (skip)
        ])

        bootstrap_prod.run_wizard(project_root, data_dir)

        config = json.loads((project_root / "server_config.json").read_text())
        assert config["login_method"] == "password"
        assert "allowed_login_domain" not in config
        # The admin is outside any allowed domain, so it is allow-listed.
        assert config["allowed_login_emails"] == ["friend@gmail.com", "me@gmail.com"]
        assert not (data_dir / "service_credentials" / "google_oauth.json").exists()
        pending_file = data_dir / "pending_admin_passwords.json"
        assert _mode(pending_file) == 0o600
        pending = read_pending_admin_passwords(data_dir)
        assert verify_password("correct horse battery", pending["me@gmail.com"])
        assert bootstrap_prod.missing_required_config(project_root, data_dir) == []

    def test_existing_google_deployment_defaults_to_google(self, deployment, monkeypatch):
        """A pre-password-sign-in install (Google OAuth configured, no
        login_method) keeps Google sign-in when the wizard is re-run."""
        project_root, data_dir = deployment
        (project_root / "server_config.json").write_text(json.dumps({
            "admin_emails": ["ops@otherco.com"], "allowed_login_domain": "otherco.com",
            "app_base_url": "https://q.otherco.com",
            "anthropic": {"vertex_project_id": "p"},
        }))
        store = data_dir / "service_credentials"
        store.mkdir()
        (store / "google_oauth.json").write_text(json.dumps({"web": {"client_id": "x"}}))
        prompts = []

        def _input(prompt=""):
            prompts.append(prompt)
            return ""

        monkeypatch.setattr("builtins.input", _input)
        # Not forced: only the missing sign-in method is asked for.
        bootstrap_prod.run_wizard(project_root, data_dir)
        assert any("[google]" in p for p in prompts)
        config = json.loads((project_root / "server_config.json").read_text())
        assert config["login_method"] == "google"


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
