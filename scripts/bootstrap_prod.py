"""First-run configuration wizard for production deployments.

Invoked by run.py when starting with --prod and the deployment is missing
the configuration a usable production instance cannot run without:

- ``admin_emails`` in server_config.json (with an empty list nobody can
  reach the admin UI, including the Settings > Service Credentials section
  used to configure everything else)
- a way to sign in, per the ``login_method`` key in server_config.json:
  for ``"password"`` (email + password accounts -- the wizard's default for
  new deployments, no external setup needed) a password for at least one
  admin account; for ``"google"`` (also what an absent key means, so
  deployments that predate password sign-in are unchanged) Google OAuth
  client credentials, without which no user can ever log in

The wizard also prompts for the login email domain, the public URL the
deployment is accessed through (used for OAuth callback URLs and
app-generated absolute links), and LLM credentials (a Vertex AI
service-account key + project id; all models run on Vertex) -- those are
not re-prompted on later runs once configured. Answers are written to
server_config.json, the per-service credential store
(<data_dir>/service_credentials/google_oauth.json),
<data_dir>/vertex-service-account.json (the service-account key copy that
run.py exports as GOOGLE_APPLICATION_CREDENTIALS in staging/prod) and, for
password sign-in, <data_dir>/pending_admin_passwords.json (scrypt hashes of
the admin passwords -- the database does not exist yet, so the server
applies and deletes this file on its next startup).

When the deployment is unconfigured but stdin is not a terminal (e.g. a
systemd unit), startup aborts with instructions instead of launching a
server nobody can log into; QUEST_SKIP_BOOTSTRAP=1 overrides the abort for
automation that intentionally starts unconfigured.

IMPORTANT: like run.py, this module uses ONLY Python 3 standard library
modules so it can run before any dependency installation has happened.
"""

import getpass
import json
import os
import sqlite3
import sys
from pathlib import Path

from config.password_hashing import (
    hash_password,
    password_problem,
    read_pending_admin_passwords,
    write_pending_admin_passwords,
)

# Env var escape hatch: skip the non-interactive abort and start anyway.
SKIP_BOOTSTRAP_ENV = "QUEST_SKIP_BOOTSTRAP"

# Written into the data directory; run.py exports it as
# GOOGLE_APPLICATION_CREDENTIALS in staging/prod when present.
VERTEX_KEY_FILENAME = "vertex-service-account.json"

# Standard Google OAuth endpoint values, mirroring what the admin Settings
# form saves (_GOOGLE_OAUTH_WEB_DEFAULTS in chat/routes/admin.py) so the
# stored config matches a Google Cloud Console client-secret JSON.
GOOGLE_OAUTH_WEB_DEFAULTS = {
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
}

# Both redirect paths must be registered on the Google OAuth client: login
# and the separate Google-services (Gmail/Calendar/Drive) consent flow.
OAUTH_REDIRECT_PATHS = ("/auth/callback", "/auth/google-services/callback")

# Sign-in methods (mirrors LOGIN_METHODS in auth/config.py). An absent key
# means Google sign-in for the running app; the wizard defaults new
# deployments to password sign-in.
LOGIN_METHOD_PASSWORD = "password"
LOGIN_METHOD_GOOGLE = "google"

# Public mailbox providers: never suggested as the allowed login domain
# (admitting "gmail.com" would admit every Gmail user).
PUBLIC_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "fastmail.com",
})


def _read_json_object(path: Path) -> dict:
    """Read a JSON file, returning {} unless it holds an object."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_private_json(path: Path, data: dict) -> None:
    """Write a JSON file created with 0600 permissions."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def google_oauth_configured(project_root: Path, data_dir: Path) -> bool:
    """Whether Google OAuth client credentials exist in the store or legacy file.

    Mirrors the store-wins-over-legacy precedence of
    config/service_credentials.py without importing app code.
    """
    store_file = data_dir / "service_credentials" / "google_oauth.json"
    if _read_json_object(store_file):
        return True
    legacy = _read_json_object(project_root / "server_credentials.json")
    return bool(legacy.get("google_oauth"))


def llm_configured(project_root: Path) -> bool:
    """Whether the Vertex LLM credentials are configured.

    All models run on Vertex AI and need a vertex_project_id (either
    provider section). Env-var overrides (ANTHROPIC_VERTEX_PROJECT_ID
    etc.) count too.
    """
    config = _read_json_object(project_root / "server_config.json")
    return bool(
        os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
        or os.environ.get("GEMINI_VERTEX_PROJECT_ID")
        or config.get("anthropic", {}).get("vertex_project_id")
        or config.get("gemini_vertex", {}).get("vertex_project_id")
    )


def configured_login_method(config: dict) -> str | None:
    """The explicit ``login_method`` in server_config.json, or None."""
    value = str(config.get("login_method") or "").strip().lower()
    return value if value in (LOGIN_METHOD_PASSWORD, LOGIN_METHOD_GOOGLE) else None


def emails_with_password(data_dir: Path) -> set:
    """Lowercased emails that have (or are about to get) a sign-in password.

    Reads the users table with the stdlib sqlite3 module (the database may
    not exist yet, or predate the password_hash column) plus the pending
    file the wizard hands to the server.
    """
    emails = set(read_pending_admin_passwords(data_dir))
    db_path = data_dir / "quest.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT email FROM users WHERE password_hash IS NOT NULL"
                ).fetchall()
            finally:
                conn.close()
            emails.update(str(row[0]).lower() for row in rows)
        except sqlite3.Error:
            pass
    return emails


def missing_required_config(project_root: Path, data_dir: Path) -> list:
    """Human-readable list of hard prerequisites this deployment is missing.

    Only the items that make the instance unusable for everyone gate the
    wizard: admin_emails (nobody can administer) and a way to sign in --
    an admin password for password sign-in, Google OAuth client
    credentials for Google sign-in. Everything else degrades to a
    feature-level error and is configurable later through the admin
    Settings UI.
    """
    missing = []
    config = _read_json_object(project_root / "server_config.json")
    admin_emails = [str(e).strip().lower() for e in config.get("admin_emails") or []]
    if not admin_emails:
        missing.append("admin_emails in server_config.json (nobody can reach the admin UI)")
    method = configured_login_method(config)
    if method == LOGIN_METHOD_PASSWORD:
        if admin_emails and not (set(admin_emails) & emails_with_password(data_dir)):
            missing.append("a password for an admin account (nobody can sign in)")
    elif not google_oauth_configured(project_root, data_dir):
        if method is None:
            missing.append("a sign-in method: email + password, or Google OAuth "
                           "client credentials (sign-in is impossible without one)")
        else:
            missing.append("Google OAuth client credentials (sign-in is impossible without them)")
    return missing


def _prompt(question: str, default: str = "") -> str:
    """Prompt for a single value; Enter accepts the default."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{question}{suffix}: ").strip()
    return value or default


def _prompt_secret(question: str) -> str:
    """Prompt for a secret without echoing it."""
    return getpass.getpass(f"{question} (input hidden): ").strip()


def _prompt_admin_emails() -> list:
    """Prompt for one or more admin email addresses until valid."""
    while True:
        raw = input("Admin email address(es), comma-separated: ").strip()
        emails = [e.strip() for e in raw.split(",") if e.strip()]
        if emails and all("@" in e and "." in e.rsplit("@", 1)[-1] for e in emails):
            return emails
        print("  ! Enter at least one full email address (e.g. admin@example.com).")


def _prompt_login_method(default: str) -> str:
    """Prompt for the sign-in method until it is one of the two choices."""
    print("Sign-in method:")
    print("  password -- email + password accounts managed in Quest. Nothing to")
    print("              set up outside Quest; the easiest way to try it out.")
    print("  google   -- Google sign-in. Needs a Google Cloud OAuth client.")
    print("You can switch a password deployment to Google sign-in later from")
    print("Settings > Sign-in without losing any accounts.")
    while True:
        value = _prompt("Sign-in method (password/google)", default).strip().lower()
        if value in (LOGIN_METHOD_PASSWORD, LOGIN_METHOD_GOOGLE):
            return value
        print('  ! Enter "password" or "google".')


def _prompt_new_password(email: str, required: bool) -> str:
    """Prompt (twice) for *email*'s password; "" when skipped."""
    while True:
        first = _prompt_secret(
            f"Password for {email}" + ("" if required else " (Enter to skip)")
        )
        if not first:
            if required:
                print("  ! At least one admin needs a password to be able to sign in.")
                continue
            return ""
        problem = password_problem(first)
        if problem:
            print(f"  ! {problem}")
            continue
        if _prompt_secret("Repeat the password") != first:
            print("  ! The passwords did not match.")
            continue
        return first


def _prompt_admin_passwords(admin_emails: list, have_password: set, force: bool) -> dict:
    """Collect ``{email: password_hash}`` for admins that need a password."""
    print()
    print("--- Admin sign-in ---")
    print("Choose the password each admin signs in with. Admins invite everyone")
    print("else from Settings > Sign-in once the server is running.")
    hashes = {}
    for email in admin_emails:
        if email.lower() in have_password and not force:
            continue
        # One admin password is required unless some admin already has one.
        required = not hashes and not (have_password & {e.lower() for e in admin_emails})
        password = _prompt_new_password(email, required)
        if password:
            hashes[email.lower()] = hash_password(password)
    return hashes


def _prompt_google_oauth(base_url: str, existing: dict) -> dict:
    """Prompt for Google OAuth client credentials.

    Returns the ``{"web": {...}}`` client-config shape the login flow
    expects, or {} when the operator skips the section. When re-running over
    an existing config (--bootstrap), an empty secret keeps the stored one
    (same convention as the admin Settings form).
    """
    base = base_url or "https://<your-server-hostname>"
    existing_web = existing.get("web", existing.get("installed", {})) or {}
    print()
    print("--- Google OAuth (login + Google services) ---")
    print("Create an OAuth client in Google Cloud Console (APIs & Services >")
    print("Credentials > Create credentials > OAuth client ID, type 'Web")
    print("application') and register BOTH authorized redirect URIs:")
    for path in OAUTH_REDIRECT_PATHS:
        print(f"  {base}{path}")
    print("Users of this deployment must sign in with Google accounts on the")
    print("allowed login domain (or the additional-emails allow-list)")
    print("configured above.")
    client_id = _prompt("OAuth client id (Enter to skip for now)", existing_web.get("client_id", ""))
    if not client_id:
        return {}
    client_secret = _prompt_secret("OAuth client secret")
    if not client_secret:
        if existing_web.get("client_secret"):
            client_secret = existing_web["client_secret"]
            print("  Keeping the previously stored client secret.")
        else:
            while not client_secret:
                client_secret = _prompt_secret("OAuth client secret (required)")
    project_id = _prompt("Google Cloud project id of the OAuth client (optional)",
                         existing_web.get("project_id", ""))
    web = {
        "client_id": client_id,
        "client_secret": client_secret,
        **GOOGLE_OAUTH_WEB_DEFAULTS,
    }
    if project_id:
        web["project_id"] = project_id
    if base_url:
        web["redirect_uris"] = [f"{base_url}{path}" for path in OAUTH_REDIRECT_PATHS]
    return {"web": web}


def _prompt_vertex_key(data_dir: Path) -> dict:
    """Prompt for a GCP service-account key file and copy it into data_dir.

    Returns the parsed key object ({} when skipped). The copy at
    <data_dir>/vertex-service-account.json is exported as
    GOOGLE_APPLICATION_CREDENTIALS by run.py in staging/prod.
    """
    while True:
        raw = _prompt("Path to a GCP service-account key JSON file (Enter to skip)")
        if not raw:
            return {}
        key_file = Path(raw).expanduser()
        try:
            with open(key_file) as f:
                key = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  ! Could not read {key_file} as JSON: {e}")
            continue
        if not isinstance(key, dict) or key.get("type") != "service_account":
            print(f"  ! {key_file} does not look like a service-account key "
                  '(expected a JSON object with "type": "service_account").')
            continue
        target = data_dir / VERTEX_KEY_FILENAME
        _write_private_json(target, key)
        print(f"  Copied key to {target} (it becomes GOOGLE_APPLICATION_CREDENTIALS at startup).")
        return key


def _merge_server_config(project_root: Path, updates: dict) -> Path:
    """Merge updates into server_config.json, preserving unrelated keys.

    Nested provider sections (e.g. "anthropic") are merged key-by-key so an
    existing vertex_region survives a project-id update.
    """
    path = project_root / "server_config.json"
    config = _read_json_object(path)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    return path


def _store_google_oauth(data_dir: Path, credentials: dict) -> Path:
    """Write google_oauth credentials into the per-service store.

    Mirrors config/service_credentials.py layout (directory 0700, file 0600)
    without importing app code.
    """
    store = data_dir / "service_credentials"
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = store / "google_oauth.json"
    _write_private_json(target, credentials)
    return target


def run_wizard(project_root: Path, data_dir: Path, force: bool = False) -> None:
    """Interactively collect and write the production configuration.

    Prompts only for sections that are missing unless ``force`` re-opens
    everything (with existing values as defaults). Answers are gathered
    first and written at the end so Ctrl+C never leaves partial config.
    """
    config = _read_json_object(project_root / "server_config.json")
    written = []

    print()
    print("=" * 60)
    print("Quest production bootstrap")
    print("=" * 60)
    missing = missing_required_config(project_root, data_dir)
    if missing:
        print("This deployment is not configured yet. Missing:")
        for item in missing:
            print(f"  - {item}")
    print("Press Enter to accept [defaults]. Ctrl+C aborts without writing.")
    print()

    updates = {}

    # Sign-in method: password sign-in needs nothing outside Quest, so it is
    # the default for new deployments. A deployment that already has Google
    # OAuth credentials but no explicit method is a pre-password-sign-in
    # install running on Google sign-in; keep that as its default.
    method = configured_login_method(config)
    if method is None or force:
        default_method = method or (
            LOGIN_METHOD_GOOGLE if google_oauth_configured(project_root, data_dir)
            else LOGIN_METHOD_PASSWORD
        )
        method = _prompt_login_method(default_method)
        print()
    if method != configured_login_method(config):
        updates["login_method"] = method
    password_login = method == LOGIN_METHOD_PASSWORD

    # Admin emails: whoever administers the deployment, incl. the Settings >
    # Service Credentials section used to configure the other connectors.
    admin_emails = config.get("admin_emails") or []
    if not admin_emails or force:
        admin_emails = _prompt_admin_emails()
        updates["admin_emails"] = admin_emails

    # Allowed login domain: sign-in (and, for password sign-in, self-service
    # account creation) is refused for accounts outside this domain and the
    # additional-emails list below, so every deployment sets its own (the
    # built-in ALLOWED_DOMAIN default is only a placeholder). Public mailbox
    # domains are never suggested: that would admit everyone on them.
    domain = config.get("allowed_login_domain", "")
    if not domain or force:
        admin_domain = admin_emails[0].rsplit("@", 1)[-1].lower() if admin_emails else ""
        default_domain = domain or (
            "" if admin_domain in PUBLIC_EMAIL_DOMAINS else admin_domain
        )
        if password_login:
            question = ("Email domain whose users may have accounts, e.g. your "
                        "company domain (Enter for none)")
        else:
            question = "Email domain allowed to sign in (users must have Google accounts on it)"
        domain = _prompt(question, default_domain).lstrip("@")
        if domain:
            updates["allowed_login_domain"] = domain

    # Individual allowed emails: accounts outside the domain (e.g. personal
    # @gmail.com users on a family deployment) that may also sign in.
    emails = list(config.get("allowed_login_emails") or [])
    if not emails or force:
        if password_login:
            print("Admins can also invite individual addresses later from Settings > Sign-in.")
        while True:
            raw = _prompt(
                "Additional email address(es) allowed to sign in, "
                "comma-separated (Enter for none)",
                ",".join(emails),
            )
            emails = [e.strip() for e in raw.split(",") if e.strip()]
            if all("@" in e and "." in e.rsplit("@", 1)[-1] for e in emails):
                break
            print("  ! Enter full email addresses (e.g. person@gmail.com).")
    # Admins must be able to sign in themselves: allow-list any admin
    # outside the allowed domain.
    for admin in admin_emails:
        if domain and admin.lower().endswith("@" + domain.lower()):
            continue
        if admin.lower() not in {e.lower() for e in emails}:
            emails.append(admin)
    if emails != list(config.get("allowed_login_emails") or []):
        updates["allowed_login_emails"] = emails

    # Public URL: the address users browse to. Used as the base for OAuth
    # redirect URIs (Google rejects raw-IP redirect URIs), shown in the
    # redirect-URI instructions below, and by app-generated absolute links
    # (e.g. the Slack "created using Quest" attribution). A legacy
    # hostname-only oauth_hostname seeds the default on re-runs.
    base_url = config.get("app_base_url", "").rstrip("/")
    if not base_url and config.get("oauth_hostname"):
        base_url = f"https://{config['oauth_hostname']}"
    if not base_url or force:
        base_url = _prompt(
            "Public URL users will browse to, e.g. https://quest.example.com "
            "(Enter to use the request host)",
            base_url,
        ).rstrip("/")
        if base_url:
            if "://" not in base_url:
                base_url = f"https://{base_url}"
            updates["app_base_url"] = base_url

    google_oauth = {}
    admin_password_hashes = {}
    if password_login:
        admin_password_hashes = _prompt_admin_passwords(
            admin_emails, emails_with_password(data_dir), force,
        )
    elif not google_oauth_configured(project_root, data_dir) or force:
        store_file = data_dir / "service_credentials" / "google_oauth.json"
        google_oauth = _prompt_google_oauth(base_url, _read_json_object(store_file))

    # LLM credentials: without them, the app runs but chat is disabled
    # (empty model picker).
    vertex_project = ""
    if not llm_configured(project_root) or force:
        print()
        print("--- LLM models (Vertex AI) ---")
        print("Claude and Gemini models run on Vertex AI: they need a GCP")
        print("service-account key (Vertex AI User role) and the project id.")
        key = _prompt_vertex_key(data_dir)
        vertex_project = _prompt(
            "GCP project id for Vertex AI models",
            key.get("project_id", "")
            or config.get("anthropic", {}).get("vertex_project_id", ""),
        )
        if vertex_project:
            updates["anthropic"] = {"vertex_project_id": vertex_project}

    # Write phase -- everything was collected above, so a Ctrl+C during the
    # prompts never leaves partial config behind.
    if updates:
        written.append(str(_merge_server_config(project_root, updates)))
    if google_oauth:
        written.append(str(_store_google_oauth(data_dir, google_oauth)))
    if admin_password_hashes:
        written.append(str(write_pending_admin_passwords(data_dir, admin_password_hashes)))

    print()
    print("Bootstrap complete." if written else "Bootstrap made no changes.")
    for path in written:
        print(f"  Wrote {path}")
    remaining = missing_required_config(project_root, data_dir)
    for item in remaining:
        print(f"  ! Still missing: {item}")
    if not llm_configured(project_root):
        print("  ! No LLM credentials configured: the app will run but chat is disabled.")
    print()
    if password_login:
        print("Sign in with an admin email and the password chosen above, then")
        print("invite users from Settings > Sign-in. Configure outgoing email")
        print("(Settings > Service Credentials > Outgoing email) to let users")
        print("reset their own passwords and create accounts on the allowed")
        print("domain. Google OAuth (for Gmail/Calendar/Drive connectors, or to")
        print("switch to Google sign-in later) is configured there as well.")
        print()
    print("Other connectors (Slack, GitHub, Twitter/X, Ramp) are configured")
    print("after login by an admin under Settings > Service Credentials.")
    print()


def maybe_run_bootstrap(project_root: Path, data_dir: Path, force: bool = False) -> None:
    """Run the wizard when the prod deployment is unconfigured (or forced).

    Non-interactive unconfigured startups abort with instructions rather
    than launching a server nobody can log into; QUEST_SKIP_BOOTSTRAP=1
    starts anyway.
    """
    missing = missing_required_config(project_root, data_dir)
    if not missing and not force:
        return

    if not sys.stdin.isatty():
        if force or not missing:
            return
        if os.environ.get(SKIP_BOOTSTRAP_ENV):
            print("! Deployment is not fully configured (QUEST_SKIP_BOOTSTRAP set, continuing):")
            for item in missing:
                print(f"  - {item}")
            return
        print("! This production deployment is not configured yet. Missing:")
        for item in missing:
            print(f"  - {item}")
        print()
        print("Run `python run.py --prod` from an interactive terminal to go")
        print("through the first-run bootstrap wizard, or create the files by")
        print("hand (see docs/setup/production.md). Set QUEST_SKIP_BOOTSTRAP=1")
        print("to start anyway.")
        sys.exit(1)

    run_wizard(project_root, data_dir, force=force)
