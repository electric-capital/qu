"""Guide data access layer.

Provides async CRUD operations for user guides (named system prompt presets).
Each user has a default guide; additional named guides can be created.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, delete

from db.engine import AsyncSessionLocal
from db.models import Guide, Routine, User


# Maximum guide content size: 16KB
MAX_GUIDE_CONTENT_SIZE = 16 * 1024

# Maximum guide name length
MAX_GUIDE_NAME_LENGTH = 100


async def create_guide(
    user_id: int,
    name: str,
    content: str = "",
    is_default: bool = False,
) -> dict:
    """Create a new guide for a user.

    Args:
        user_id: User's integer ID.
        name: Guide name (max 100 chars, unique per user).
        content: Guide content / system prompt text.
        is_default: Whether this is the default guide.

    Returns:
        Guide dict.

    Raises:
        ValueError: If name is empty, too long, or content exceeds max size.
    """
    if not name or not name.strip():
        raise ValueError("Guide name cannot be empty.")
    if len(name) > MAX_GUIDE_NAME_LENGTH:
        raise ValueError(
            f"Guide name exceeds maximum length of {MAX_GUIDE_NAME_LENGTH} characters."
        )
    if len(content.encode("utf-8")) > MAX_GUIDE_CONTENT_SIZE:
        raise ValueError(
            f"Guide content exceeds maximum size of {MAX_GUIDE_CONTENT_SIZE} bytes."
        )

    async with AsyncSessionLocal() as db:
        guide = Guide(
            user_id=user_id,
            name=name.strip(),
            content=content,
            is_default=is_default,
        )
        db.add(guide)
        await db.commit()
        await db.refresh(guide)
        return _guide_to_dict(guide)


async def get_guide(user_id: int, guide_id: str) -> Optional[dict]:
    """Get a specific guide by ID, scoped to a user.

    Args:
        user_id: User's integer ID.
        guide_id: Guide UUID string.

    Returns:
        Guide dict or None if not found.
    """
    async with AsyncSessionLocal() as db:
        guide = await db.get(Guide, guide_id)
        if guide and guide.user_id == user_id:
            return _guide_to_dict(guide)
        return None


async def get_default_guide(user_id: int) -> Optional[dict]:
    """Get the default guide for a user.

    Args:
        user_id: User's integer ID.

    Returns:
        Guide dict or None if no default exists.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Guide).where(Guide.user_id == user_id, Guide.is_default == True)
        )
        guide = result.scalars().first()
        if guide:
            return _guide_to_dict(guide)
        return None


async def list_guides(user_id: int) -> list[dict]:
    """List all guides for a user.

    Returns default guide first, then others alphabetically by name.

    Args:
        user_id: User's integer ID.

    Returns:
        List of guide dicts.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Guide)
            .where(Guide.user_id == user_id)
            .order_by(Guide.is_default.desc(), Guide.name.asc())
        )
        guides = result.scalars().all()
        return [_guide_to_dict(g) for g in guides]


async def list_all_guides() -> list[dict]:
    """List every guide across all users (admin reporting).

    Returns rows with the owner's email/name and the number of routines that
    still reference the guide as an override (the only remaining source of
    guide usage since composer selection was removed). Guide content is
    reported as ``content_length`` rather than shipped verbatim -- the admin
    report only needs to distinguish empty auto-created default guides from
    real ones. Ordered by owner email, default guide first, then name.
    """
    routine_counts = (
        select(Routine.guide_id, func.count(Routine.id).label("routine_count"))
        .where(Routine.guide_id.is_not(None))
        .group_by(Routine.guide_id)
        .subquery()
    )
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(
                Guide,
                User.email,
                User.name,
                func.coalesce(routine_counts.c.routine_count, 0),
            )
            .join(User, User.id == Guide.user_id)
            .outerjoin(routine_counts, routine_counts.c.guide_id == Guide.id)
            .order_by(User.email.asc(), Guide.is_default.desc(), Guide.name.asc())
        )
        rows = []
        for guide, email, name, routine_count in result.all():
            rows.append({
                "id": guide.id,
                "user_id": guide.user_id,
                "user_email": email,
                "user_name": name or "",
                "name": guide.name,
                "is_default": bool(guide.is_default),
                "content_length": len(guide.content or ""),
                "routine_count": int(routine_count or 0),
                "created_at": guide.created_at.isoformat() if guide.created_at else None,
                "updated_at": guide.updated_at.isoformat() if guide.updated_at else None,
            })
        return rows


async def update_guide(
    user_id: int,
    guide_id: str,
    name: Optional[str] = None,
    content: Optional[str] = None,
) -> Optional[dict]:
    """Update a guide's name and/or content.

    The default guide cannot be renamed (its name is always "Default").

    Args:
        user_id: User's integer ID.
        guide_id: Guide UUID string.
        name: New name (optional, max 100 chars).
        content: New content (optional).

    Returns:
        Updated guide dict, or None if not found.

    Raises:
        ValueError: If name is empty, too long, content too large,
            or trying to rename the default guide.
    """
    if name is not None:
        if not name or not name.strip():
            raise ValueError("Guide name cannot be empty.")
        if len(name) > MAX_GUIDE_NAME_LENGTH:
            raise ValueError(
                f"Guide name exceeds maximum length of {MAX_GUIDE_NAME_LENGTH} characters."
            )

    if content is not None:
        if len(content.encode("utf-8")) > MAX_GUIDE_CONTENT_SIZE:
            raise ValueError(
                f"Guide content exceeds maximum size of {MAX_GUIDE_CONTENT_SIZE} bytes."
            )

    async with AsyncSessionLocal() as db:
        guide = await db.get(Guide, guide_id)
        if not guide or guide.user_id != user_id:
            return None

        # Default guide cannot be renamed
        if guide.is_default and name is not None and name.strip() != guide.name:
            raise ValueError("Cannot rename the default guide.")

        if name is not None:
            guide.name = name.strip()
        if content is not None:
            guide.content = content

        guide.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(guide)
        return _guide_to_dict(guide)


async def delete_guide(user_id: int, guide_id: str) -> bool:
    """Delete a guide by ID, scoped to a user.

    Guides are deprecated in favor of skills, so the default guide is
    deletable like any other (it is no longer auto-recreated).

    Args:
        user_id: User's integer ID.
        guide_id: Guide UUID string.

    Returns:
        True if deleted, False if not found.
    """
    async with AsyncSessionLocal() as db:
        guide = await db.get(Guide, guide_id)
        if not guide or guide.user_id != user_id:
            return False
        await db.delete(guide)
        await db.commit()
        return True


async def delete_all_user_guides(user_id: int) -> int:
    """Delete all guides for a user.

    Used during account deletion to cascade-remove guide data.

    Args:
        user_id: User's integer ID.

    Returns:
        Count of deleted guides.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(Guide).where(Guide.user_id == user_id)
        )
        await db.commit()
        return result.rowcount


def _guide_to_dict(guide: Guide) -> dict:
    """Convert a Guide ORM instance to a plain dict."""
    return {
        "id": guide.id,
        "user_id": guide.user_id,
        "name": guide.name,
        "content": guide.content,
        "is_default": guide.is_default,
        "created_at": guide.created_at.isoformat() if guide.created_at else None,
        "updated_at": guide.updated_at.isoformat() if guide.updated_at else None,
    }
