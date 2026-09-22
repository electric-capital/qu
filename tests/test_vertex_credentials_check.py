"""Tests for the startup Vertex ADC-vs-config consistency check.

check_vertex_credentials_consistency() compares the service-account key file
pointed at by GOOGLE_APPLICATION_CREDENTIALS against the vertex_project_id
values in the server config, logging an error on a project mismatch and a
warning when credentials are present but no project id is configured (so
Vertex models are silently hidden from the picker). It must never raise.
"""

import json
import logging
from unittest.mock import patch

from config.server_config import check_vertex_credentials_consistency


def _config(anthropic_project="", gemini_vertex_project=""):
    return {
        "anthropic": {"vertex_project_id": anthropic_project},
        "gemini_vertex": {"vertex_project_id": gemini_vertex_project},
    }


def _write_key(tmp_path, project_id="proj-a"):
    key_file = tmp_path / "vertex-service-account.json"
    key_file.write_text(json.dumps({"type": "service_account", "project_id": project_id}))
    return key_file


class TestVertexCredentialsConsistency:
    def test_silent_without_adc_env(self, monkeypatch, caplog):
        """No GOOGLE_APPLICATION_CREDENTIALS -> nothing to compare, no logs."""
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        with caplog.at_level(logging.DEBUG, logger="config.server_config"):
            check_vertex_credentials_consistency()
        assert caplog.records == []

    def test_silent_on_match(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(_write_key(tmp_path, "proj-a"))
        )
        with patch(
            "config.server_config.load_server_config",
            return_value=_config("proj-a", "proj-a"),
        ), caplog.at_level(logging.DEBUG, logger="config.server_config"):
            check_vertex_credentials_consistency()
        assert caplog.records == []

    def test_error_on_project_mismatch(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(_write_key(tmp_path, "proj-a"))
        )
        with patch(
            "config.server_config.load_server_config",
            return_value=_config("proj-b", "proj-b"),
        ), caplog.at_level(logging.ERROR, logger="config.server_config"):
            check_vertex_credentials_consistency()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 2  # one per configured section
        assert "'proj-b'" in errors[0].getMessage()
        assert "'proj-a'" in errors[0].getMessage()

    def test_error_only_for_mismatched_section(self, monkeypatch, tmp_path, caplog):
        """A matching section stays quiet while the mismatched one errors."""
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(_write_key(tmp_path, "proj-a"))
        )
        with patch(
            "config.server_config.load_server_config",
            return_value=_config("proj-a", "proj-b"),
        ), caplog.at_level(logging.ERROR, logger="config.server_config"):
            check_vertex_credentials_consistency()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "gemini_vertex" in errors[0].getMessage()

    def test_warns_when_key_present_but_nothing_configured(
        self, monkeypatch, tmp_path, caplog
    ):
        """The case this check exists for: working ADC key, empty config."""
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(_write_key(tmp_path, "proj-a"))
        )
        with patch(
            "config.server_config.load_server_config", return_value=_config()
        ), caplog.at_level(logging.WARNING, logger="config.server_config"):
            check_vertex_credentials_consistency()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "no vertex_project_id is" in warnings[0].getMessage()

    def test_warns_on_unreadable_key_file(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing.json")
        )
        with caplog.at_level(logging.WARNING, logger="config.server_config"):
            check_vertex_credentials_consistency()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "could not be read" in warnings[0].getMessage()

    def test_warns_on_key_without_project_id(self, monkeypatch, tmp_path, caplog):
        key_file = tmp_path / "key.json"
        key_file.write_text(json.dumps({"type": "service_account"}))
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key_file))
        with caplog.at_level(logging.WARNING, logger="config.server_config"):
            check_vertex_credentials_consistency()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "no project_id" in warnings[0].getMessage()
