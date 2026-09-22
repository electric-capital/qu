"""SQLAlchemy column types that encrypt secrets at rest.

``EncryptedText`` / ``EncryptedJSON`` wrap the existing ``Text`` /
``String`` / ``JSON`` column types so the ORM transparently encrypts on
write and decrypts on read via ``config/encryption.py``; store modules and
plugins keep handling plain values. Each column passes a stable ``aad``
label (``"<table>.<column>"``) that is bound into the AES-GCM tag, so a
ciphertext moved between columns fails authentication.

The impl types deliberately stay what the columns were declared as before
encryption (``String(64)`` for ``users.api_key``, ``JSON`` for the token
blobs, ...) so Alembic autogenerate does not see a type change; SQLite
ignores VARCHAR lengths, so the longer ciphertext fits. For ``JSON``
columns the stored value is the JSON string literal of the envelope
(``"qenc1:..."``), which is what SQLAlchemy's JSON type produces when
handed a str.

A stored value that is NOT a ``qenc1:`` envelope is returned as legacy
plaintext (with a one-time warning per column): migration
``c4e8a2d17f63`` encrypts every existing row, and the tolerance covers a
row written by a pre-upgrade process that was still running.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.types import TypeDecorator

from config import encryption

logger = logging.getLogger(__name__)

_warned_plaintext: set[str] = set()


def _warn_plaintext(aad: str) -> None:
    if aad not in _warned_plaintext:
        _warned_plaintext.add(aad)
        logger.warning(
            "Read a legacy plaintext value from %s; run the database migration "
            "(alembic upgrade head) to encrypt existing rows",
            aad,
        )


class EncryptedText(TypeDecorator):
    """A ``Text``/``String`` column whose value is encrypted at rest."""

    impl = sa.Text
    cache_ok = True

    def __init__(self, aad: str, length: int | None = None):
        self.aad = aad
        self.length = length
        super().__init__()

    def load_dialect_impl(self, dialect):
        if self.length is not None:
            return dialect.type_descriptor(sa.String(self.length))
        return dialect.type_descriptor(sa.Text())

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{self.aad} expects a str, got {type(value).__name__}")
        return encryption.encrypt_str(value, self.aad)

    def process_result_value(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        if encryption.is_encrypted(value):
            return encryption.decrypt_str(value, self.aad)
        _warn_plaintext(self.aad)
        return value


class EncryptedJSON(TypeDecorator):
    """A ``JSON`` column whose serialized document is encrypted at rest."""

    impl = sa.JSON
    cache_ok = True

    def __init__(self, aad: str):
        self.aad = aad
        super().__init__()

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        return encryption.encrypt_json(value, self.aad)

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if encryption.is_encrypted(value):
            return encryption.decrypt_json(value, self.aad)
        _warn_plaintext(self.aad)
        return value
