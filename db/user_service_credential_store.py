"""Data access layer for per-user plugin service credentials.

Plugin-contributed services store their per-user credentials in the
``user_service_credentials`` table (one row per (user, service)) instead of
dedicated ``users`` columns. The generic key routes in ``auth/service_key.py``
write here, ``db/user_store.py`` attaches the rows to every user dict as
``user["service_credentials"]`` (a service -> row-dict map, present only when
non-empty), and ``get_user_connected_services()`` feeds each row to the
plugin's ``UserConnectionSpec.connected`` predicate.
"""

from typing import Optional

from sqlalchemy import delete, select

from db.engine import AsyncSessionLocal
from db.models import UserServiceCredential


async def get_credential(user_id: int, service: str) -> Optional[dict]:
    """Return one user's credential row for a service, or None."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserServiceCredential).where(
                UserServiceCredential.user_id == user_id,
                UserServiceCredential.service == service,
            )
        )
        row = result.scalars().first()
        return row.to_dict() if row else None


async def list_credentials(user_id: int) -> dict[str, dict]:
    """Return all of a user's credential rows as a service -> row map."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserServiceCredential).where(
                UserServiceCredential.user_id == user_id
            )
        )
        return {row.service: row.to_dict() for row in result.scalars().all()}


async def upsert_credential(
    user_id: int,
    service: str,
    *,
    secret: Optional[str] = None,
    oauth_blob: Optional[dict] = None,
) -> dict:
    """Create or replace a user's credential row for a service.

    Both credential fields are written verbatim (a full replacement, not a
    merge): saving a new API key clears any stale ``oauth_blob`` and vice
    versa.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserServiceCredential).where(
                UserServiceCredential.user_id == user_id,
                UserServiceCredential.service == service,
            )
        )
        row = result.scalars().first()
        if row is None:
            row = UserServiceCredential(user_id=user_id, service=service)
            db.add(row)
        row.secret = secret
        row.oauth_blob = oauth_blob
        await db.commit()
        await db.refresh(row)
        return row.to_dict()


async def delete_credential(user_id: int, service: str) -> bool:
    """Delete a user's credential row. Returns True if a row was removed."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(UserServiceCredential).where(
                UserServiceCredential.user_id == user_id,
                UserServiceCredential.service == service,
            )
        )
        await db.commit()
        return result.rowcount > 0


async def delete_all_credentials(user_id: int) -> int:
    """Delete all of a user's credential rows (disconnect-all paths).

    Account deletion does not need this -- the FK cascades -- but the
    logout-and-disconnect flow removes connector credentials while keeping
    the account.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(UserServiceCredential).where(
                UserServiceCredential.user_id == user_id
            )
        )
        await db.commit()
        return result.rowcount
