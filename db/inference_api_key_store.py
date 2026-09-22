"""Data access layer for inference API keys (Settings > Inference API).

Each row is a named bearer token for the one-shot ``POST /api/inference``
endpoint. Only the SHA-256 hex digest of the raw token is stored; the raw
token is shown to the user exactly once at creation. Token minting and
hashing helpers live here so the routes and the auth dependency share one
implementation.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, delete, update

from db.engine import AsyncSessionLocal
from db.models import InferenceApiKey

# Prefix makes tokens recognizable in logs/configs without revealing the
# secret part (mirrors common "sk-..." style key conventions).
TOKEN_PREFIX = "qst_"

# Per-user cap; enforced by the create route.
MAX_KEYS_PER_USER = 20


def generate_token() -> str:
    """Mint a new raw inference API token."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a raw token (the only stored form)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_hint(token: str) -> str:
    """Display hint for a raw token: its last 4 characters."""
    return token[-4:]


def _row_to_dict(row: InferenceApiKey) -> dict:
    """Public projection of a key row. Never includes token_hash."""
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "token_hint": row.token_hint,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": (
            row.last_used_at.isoformat() if row.last_used_at else None
        ),
    }


async def create_key(user_id: int, name: str, token: str) -> dict:
    """Persist a new key row for a freshly minted raw token."""
    async with AsyncSessionLocal() as db:
        row = InferenceApiKey(
            user_id=user_id,
            name=name,
            token_hash=hash_token(token),
            token_hint=token_hint(token),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _row_to_dict(row)


async def list_keys(user_id: int) -> list[dict]:
    """All keys owned by *user_id*, newest first."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InferenceApiKey)
            .where(InferenceApiKey.user_id == user_id)
            .order_by(InferenceApiKey.created_at.desc())
        )
        return [_row_to_dict(row) for row in result.scalars().all()]


async def count_keys(user_id: int) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InferenceApiKey.id).where(
                InferenceApiKey.user_id == user_id
            )
        )
        return len(result.scalars().all())


async def delete_key(user_id: int, key_id: str) -> bool:
    """Delete a key owned by *user_id*. Returns True when a row was removed."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(InferenceApiKey).where(
                InferenceApiKey.id == key_id,
                InferenceApiKey.user_id == user_id,
            )
        )
        await db.commit()
        return result.rowcount > 0


async def get_key_by_token(token: str) -> Optional[dict]:
    """Look up a key row by raw token (hashed before the query)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InferenceApiKey).where(
                InferenceApiKey.token_hash == hash_token(token)
            )
        )
        row = result.scalars().first()
        return _row_to_dict(row) if row else None


async def touch_last_used(key_id: str) -> None:
    """Best-effort bump of last_used_at for a successful authentication."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(InferenceApiKey)
            .where(InferenceApiKey.id == key_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await db.commit()
