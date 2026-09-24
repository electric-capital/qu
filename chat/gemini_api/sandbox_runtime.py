"""OCI runtime selection for the script-runner sandbox.

Every run_script / run_python call starts a fresh podman container, so the
per-container create/teardown cost of the OCI runtime is paid on every
sandbox tool call. crun (C, single-process) creates and deletes containers
markedly faster than runc (Go, re-exec'd ``runc init``): ~335 ms vs ~540 ms
for a full restricted-profile ``podman run`` on a 4-vCPU Debian host. The
runtime is selected per invocation with podman's global ``--runtime`` flag,
so no containers.conf change is needed and hosts without crun keep working
on podman's default.

Resolution, once per process:

1. ``QUEST_SANDBOX_RUNTIME`` env var, when set: ``default`` means podman's
   own default (no flag); any other value -- a runtime name such as
   ``runc`` or an absolute binary path -- is passed through verbatim so an
   admin can pin a runtime.
2. Otherwise crun when found on PATH and ``crun --version`` succeeds (a
   broken binary must not fail every sandbox run), passed as its absolute
   path.
3. Otherwise ``None``: no ``--runtime`` flag, podman's default runtime.

The sandbox confinement (seccomp profile, slirp4netns network, entrypoint
iptables rules, setpriv privilege drop) is applied by podman and the image
entrypoint, not the runtime, so both runtimes enforce the same profile.
"""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

RUNTIME_ENV_VAR = "QUEST_SANDBOX_RUNTIME"

# Override value that selects podman's default runtime (no --runtime flag).
DEFAULT_KEYWORD = "default"

_PROBE_TIMEOUT_SECONDS = 5

# Cache: (resolved,) once resolved; a 1-tuple so a resolved None is
# distinguishable from "not resolved yet".
_resolved: tuple[str | None] | None = None


def _probe_crun() -> str | None:
    """Return the absolute path of a working crun binary, or None."""
    path = shutil.which("crun")
    if not path:
        return None
    try:
        subprocess.run(
            [path, "--version"],
            check=True,
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "crun found at %s but unusable (%s); using podman's default runtime",
            path, exc,
        )
        return None
    return path


def _resolve() -> str | None:
    override = os.environ.get(RUNTIME_ENV_VAR, "").strip()
    if override:
        return None if override == DEFAULT_KEYWORD else override
    return _probe_crun()


def get_sandbox_runtime() -> str | None:
    """Return the ``podman --runtime`` value for sandbox containers.

    Returns:
        A runtime name or binary path, or None to use podman's default
        runtime (omit the flag). Resolved once per process.
    """
    global _resolved
    if _resolved is None:
        _resolved = (_resolve(),)
    return _resolved[0]


def describe_sandbox_runtime() -> str:
    """Human-readable runtime choice for the startup log."""
    runtime = get_sandbox_runtime()
    if os.environ.get(RUNTIME_ENV_VAR, "").strip():
        source = f"{RUNTIME_ENV_VAR} override"
    else:
        source = "auto-detected"
    if runtime is None:
        return f"podman default ({source})"
    return f"{runtime} ({source})"
