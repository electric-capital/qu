"""Password hashing for email/password sign-in.

scrypt from ``hashlib`` (memory-hard, no third-party dependency), stored as
a self-describing string ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>`` so the
cost parameters can be raised later without invalidating existing hashes.

Also home of the *pending admin passwords* file: the prod bootstrap wizard
runs before the database exists (and before dependencies are installed), so
it cannot write the users table. It hashes the admin password here and
drops the hash into ``<data_dir>/pending_admin_passwords.json``; the server
lifespan applies and deletes the file on the next startup
(``auth.password_login.apply_pending_admin_passwords``).

Like ``config/encryption.py``, this module is stdlib-only so the stdlib-only
bootstrap wizard can import it.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

# scrypt cost: n=2**15, r=8 -> 32 MiB of memory and ~50-100 ms per hash.
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_HASH_BYTES = 32

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256

PENDING_ADMIN_PASSWORDS_FILENAME = "pending_admin_passwords.json"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        # OpenSSL's default 32 MiB cap is exactly 128*n*r; leave headroom.
        maxmem=256 * n * r,
        dklen=_HASH_BYTES,
    )


def password_problem(password: str) -> str | None:
    """Why *password* is unacceptable, or None when it is fine.

    Length-only policy (NIST SP 800-63B): no composition rules.
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    return None


def hash_password(password: str) -> str:
    """Hash *password* with a fresh random salt."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of *password* against a stored hash string.

    Malformed or missing hashes never verify.
    """
    if not stored or not isinstance(password, str):
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = _scrypt(password, salt, int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def burn_verify_time(password: str) -> None:
    """Spend the same time as a real verification (unknown-account logins).

    Keeps the login endpoint's response time from revealing whether an
    account exists.
    """
    _scrypt(password or "", b"\0" * _SALT_BYTES, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)


def password_fingerprint(stored: str | None) -> str | None:
    """Short, non-reversible tag of the current password hash.

    Embedded in password-issued session cookies: a password change
    produces a new salt and therefore a new fingerprint, which invalidates
    every session issued under the old password.
    """
    if not stored:
        return None
    return hashlib.sha256(stored.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Pending admin passwords (bootstrap wizard -> server startup)
# ---------------------------------------------------------------------------

def pending_admin_passwords_path(data_dir: Path) -> Path:
    return Path(data_dir) / PENDING_ADMIN_PASSWORDS_FILENAME


def read_pending_admin_passwords(data_dir: Path) -> dict[str, str]:
    """``{email: password_hash}`` waiting to be applied, or {}."""
    path = pending_admin_passwords_path(data_dir)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(email).strip().lower(): value
        for email, value in data.items()
        if isinstance(value, str) and value.startswith("scrypt$")
    }


def write_pending_admin_passwords(data_dir: Path, entries: dict[str, str]) -> Path:
    """Merge ``{email: password_hash}`` into the pending file (0600)."""
    merged = read_pending_admin_passwords(data_dir)
    merged.update({email.strip().lower(): h for email, h in entries.items()})
    path = pending_admin_passwords_path(data_dir)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(merged, f, indent=2)
    return path
