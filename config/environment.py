"""Runtime environment (run mode) resolution.

Quest has three run modes, selected by the ``QUEST_ENV`` environment
variable (normally set by ``run.py``):

- ``local``:   developer machine / agent sandbox. No OAuth login (canned
               accounts via ``/auth/dev-login``), no domain restriction,
               throwaway per-run database, and missing credentials degrade
               gracefully instead of failing startup. The legacy value
               ``dev`` is accepted as an alias.
- ``staging``: automated deployment against real credentials and upstream
               integrations. Behaves like prod (domain restriction on,
               secure cookies) but uses a distinct cookie name so staging
               and prod sessions never collide in a shared browser.
- ``prod``:    production. The default when ``QUEST_ENV`` is unset or
               unrecognized (fail toward the most restrictive mode).

Consumers should use the semantic helpers below instead of comparing mode
strings, so every behavior difference between modes stays auditable in
this one file.

This module intentionally uses **only** the Python standard library so it
can be imported as early as ``config.paths``.
"""

import os

LOCAL = "local"
STAGING = "staging"
PROD = "prod"

# Legacy spellings accepted for backward compatibility.
_ALIASES = {
    "dev": LOCAL,
    "development": LOCAL,
    "production": PROD,
}
_VALID_ENVS = {LOCAL, STAGING, PROD}

# Email of the canned admin account seeded in local mode. Shared between
# the local seeder (scripts/seed_local.py) and the default admin_emails
# list applied when no server_config.json is present in local mode.
LOCAL_CANNED_ADMIN_EMAIL = "admin@quest.local"


def get_quest_env() -> str:
    """Return the canonical run mode: ``local``, ``staging``, or ``prod``.

    Reads the environment variable on every call (cheap) so tests can
    monkeypatch ``QUEST_ENV`` without module reloads.
    """
    raw = os.environ.get("QUEST_ENV", PROD).strip().lower()
    raw = _ALIASES.get(raw, raw)
    if raw not in _VALID_ENVS:
        return PROD
    return raw


def is_local() -> bool:
    return get_quest_env() == LOCAL


def is_staging() -> bool:
    return get_quest_env() == STAGING


def is_prod() -> bool:
    return get_quest_env() == PROD


# ---------------------------------------------------------------------------
# Capability flags
#
# Feature code should key off these, not off get_quest_env() comparisons.
# ---------------------------------------------------------------------------

def allow_dev_login() -> bool:
    """Canned-account / arbitrary-email login without OAuth (local only)."""
    return is_local()


def enforce_domain() -> bool:
    """Restrict access to the allowed email domain (staging and prod)."""
    return not is_local()


def cookie_name() -> str:
    """Session cookie name; per-mode so sessions never collide across modes."""
    env = get_quest_env()
    return "quest_session" if env == PROD else f"quest_session_{env}"


def cookie_secure() -> bool:
    """Whether session cookies get the ``Secure`` attribute.

    Staging is assumed to sit behind HTTPS. Prod is intentionally left
    unchanged (not secure) until its fronting is confirmed to be HTTPS-only;
    flipping it here would silently break a plain-HTTP prod deployment.
    """
    return is_staging()


def image_suffix() -> str:
    """Suffix for per-mode container image tags (quest-script-runner-*)."""
    return get_quest_env()
