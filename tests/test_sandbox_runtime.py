"""Tests for sandbox OCI runtime selection (chat/gemini_api/sandbox_runtime.py)."""

import subprocess

import pytest

import chat.gemini_api.sandbox_runtime as sandbox_runtime
import chat.gemini_api.tool_handlers.sandbox as sandbox
from chat.gemini_api.tool_handlers import _build_script_podman_cmd


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(sandbox_runtime, "_resolved", None)
    monkeypatch.delenv(sandbox_runtime.RUNTIME_ENV_VAR, raising=False)


def _fake_which(path):
    return lambda name: path if name == "crun" else None


class TestResolution:

    def test_prefers_working_crun(self, monkeypatch):
        monkeypatch.setattr(sandbox_runtime.shutil, "which", _fake_which("/usr/bin/crun"))
        calls = []
        monkeypatch.setattr(
            sandbox_runtime.subprocess, "run",
            lambda argv, **kw: calls.append(argv),
        )
        assert sandbox_runtime.get_sandbox_runtime() == "/usr/bin/crun"
        assert calls == [["/usr/bin/crun", "--version"]]

    def test_no_crun_uses_podman_default(self, monkeypatch):
        monkeypatch.setattr(sandbox_runtime.shutil, "which", _fake_which(None))
        assert sandbox_runtime.get_sandbox_runtime() is None

    def test_broken_crun_uses_podman_default(self, monkeypatch):
        monkeypatch.setattr(sandbox_runtime.shutil, "which", _fake_which("/usr/bin/crun"))

        def boom(argv, **kw):
            raise subprocess.CalledProcessError(1, argv)

        monkeypatch.setattr(sandbox_runtime.subprocess, "run", boom)
        assert sandbox_runtime.get_sandbox_runtime() is None

    def test_env_override_passes_through_without_probe(self, monkeypatch):
        monkeypatch.setenv(sandbox_runtime.RUNTIME_ENV_VAR, "runc")
        monkeypatch.setattr(
            sandbox_runtime.shutil, "which",
            lambda name: pytest.fail("override must not probe"),
        )
        assert sandbox_runtime.get_sandbox_runtime() == "runc"
        assert "override" in sandbox_runtime.describe_sandbox_runtime()

    def test_env_default_keyword_omits_flag(self, monkeypatch):
        monkeypatch.setenv(sandbox_runtime.RUNTIME_ENV_VAR, "default")
        monkeypatch.setattr(sandbox_runtime.shutil, "which", _fake_which("/usr/bin/crun"))
        assert sandbox_runtime.get_sandbox_runtime() is None

    def test_resolved_once_per_process(self, monkeypatch):
        probes = []
        monkeypatch.setattr(
            sandbox_runtime.shutil, "which",
            lambda name: probes.append(name) or None,
        )
        sandbox_runtime.get_sandbox_runtime()
        sandbox_runtime.get_sandbox_runtime()
        assert probes == ["crun"]


class TestPodmanArgv:

    def test_runtime_flag_is_global_podman_option(self, monkeypatch):
        monkeypatch.setattr(sandbox, "get_sandbox_runtime", lambda: "/usr/bin/crun")
        cmd = _build_script_podman_cmd("/ws", ["python3"], "k")
        assert cmd[:5] == ["podman", "--runtime", "/usr/bin/crun", "run", "--rm"]

    def test_no_runtime_flag_when_default(self, monkeypatch):
        monkeypatch.setattr(sandbox, "get_sandbox_runtime", lambda: None)
        cmd = _build_script_podman_cmd("/ws", ["python3"], "k")
        assert cmd[:3] == ["podman", "run", "--rm"]
        assert "--runtime" not in cmd
