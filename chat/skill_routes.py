"""REST API endpoints for Skill Library CRUD and sharing."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from chat.auth import get_current_user_cookie_or_apikey_checked
from db.skill_store import (
    create_skill,
    get_skill,
    list_accessible_skills,
    list_shared_with_me_skills,
    search_accessible_skills,
    update_skill,
    delete_skill,
    add_skill_shares,
    list_skill_shares,
    remove_skill_share,
    user_can_access_skill,
    list_user_autoloaded_skill_ids,
    set_user_skill_autoload,
    VALID_VISIBILITIES,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/app/api",
    tags=["skills"],
)


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class CreateSkillRequest(BaseModel):
    name: str
    description: str = ""
    content: str
    visibility: str = "private"


class UpdateSkillRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    visibility: Optional[str] = None


class ShareSkillRequest(BaseModel):
    emails: list[str]


class AutoloadRequest(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Skill CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("/skills", status_code=201)
async def create_user_skill(
    body: CreateSkillRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Create a new skill."""
    user_id = user["id"]

    try:
        skill = await create_skill(
            creator_id=user_id,
            name=body.name,
            description=body.description,
            content=body.content,
            visibility=body.visibility,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(e)},
        )
    except Exception as e:
        error_msg = str(e)
        if "UNIQUE constraint failed" in error_msg or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=409,
                detail={"error": "duplicate_name", "message": f"A skill named '{body.name.strip()}' already exists"},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": "create_failed", "message": error_msg},
        )

    return skill


@router.get("/skills")
async def list_skills(
    owned: bool = Query(False, description="If true, return only skills created by the current user"),
    visibility: Optional[str] = Query(None, description="Filter by visibility level"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """List skills accessible to the current user."""
    user_id = user["id"]

    try:
        skills = await list_accessible_skills(
            user_id=user_id,
            owned_only=owned,
            visibility_filter=visibility,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(e)},
        )

    return {"skills": skills}


@router.get("/skills/search")
async def search_skills_endpoint(
    q: str = Query(..., min_length=1),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Search accessible skills by keyword (name or description substring match)."""
    user_id = user["id"]
    skills = await search_accessible_skills(user_id, q)
    return {"skills": skills}


@router.get("/skills/autoloaded")
async def get_autoloaded_skill_ids(
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Get the list of skill IDs the current user has auto-loaded."""
    user_id = user["id"]
    skill_ids = await list_user_autoloaded_skill_ids(user_id)
    return {"skill_ids": skill_ids}


@router.get("/skills/shared-with-me")
async def get_shared_with_me_skills(
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """List skills accessible to the current user but not created by them."""
    user_id = user["id"]
    skills = await list_shared_with_me_skills(user_id)
    return {"skills": skills}


@router.put("/skills/{skill_id}/autoload")
async def toggle_skill_autoload(
    skill_id: str,
    body: AutoloadRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Toggle auto-load for a skill. User must have access to the skill."""
    user_id = user["id"]

    # Verify user can access the skill
    has_access = await user_can_access_skill(user_id, skill_id)
    if not has_access:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Skill not found"},
        )

    await set_user_skill_autoload(user_id, skill_id, body.enabled)
    return {"success": True}


@router.get("/skills/{skill_id}")
async def get_user_skill(
    skill_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Get a specific skill by ID (access-checked)."""
    user_id = user["id"]

    # Check access
    has_access = await user_can_access_skill(user_id, skill_id)
    if not has_access:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Skill not found"},
        )

    skill = await get_skill(skill_id)
    if not skill:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Skill not found"},
        )

    return skill


@router.put("/skills/{skill_id}")
async def update_user_skill(
    skill_id: str,
    body: UpdateSkillRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Update a skill. Only the creator can update."""
    user_id = user["id"]

    # Validate visibility if provided
    if body.visibility is not None and body.visibility not in VALID_VISIBILITIES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_error",
                "message": f"Invalid visibility '{body.visibility}'. Must be one of: {', '.join(sorted(VALID_VISIBILITIES))}",
            },
        )

    try:
        skill = await update_skill(
            creator_id=user_id,
            skill_id=skill_id,
            name=body.name,
            description=body.description,
            content=body.content,
            visibility=body.visibility,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(e)},
        )
    except Exception as e:
        error_msg = str(e)
        if "UNIQUE constraint failed" in error_msg or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=409,
                detail={"error": "duplicate_name", "message": "A skill with that name already exists"},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": "update_failed", "message": error_msg},
        )

    if not skill:
        # Could be not found or not the creator -- check which
        existing = await get_skill(skill_id)
        if existing:
            raise HTTPException(
                status_code=403,
                detail={"error": "forbidden", "message": "Only the skill creator can update it"},
            )
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Skill not found"},
        )

    return skill


@router.delete("/skills/{skill_id}")
async def delete_user_skill(
    skill_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Delete a skill. Only the creator can delete."""
    user_id = user["id"]

    deleted = await delete_skill(creator_id=user_id, skill_id=skill_id)

    if not deleted:
        # Could be not found or not the creator -- check which
        existing = await get_skill(skill_id)
        if existing:
            raise HTTPException(
                status_code=403,
                detail={"error": "forbidden", "message": "Only the skill creator can delete it"},
            )
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Skill not found"},
        )

    return {"success": True}


# ---------------------------------------------------------------------------
# Skill sharing endpoints
# ---------------------------------------------------------------------------


@router.post("/skills/{skill_id}/shares")
async def add_skill_shares_endpoint(
    skill_id: str,
    body: ShareSkillRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Share a skill with users by email. Only the creator can manage shares."""
    user_id = user["id"]

    # Verify skill exists and user is creator
    skill = await get_skill(skill_id)
    if not skill:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Skill not found"},
        )
    if skill["creator_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Only the skill creator can manage shares"},
        )

    # Resolve emails to user IDs (silently skip unknown emails)
    from db.user_store import get_user_by_email

    resolved_user_ids = []
    for email in body.emails:
        target_user = await get_user_by_email(email.strip())
        if target_user and target_user["id"] != user_id:
            # Don't share with the creator themselves
            resolved_user_ids.append(target_user["id"])

    shares = await add_skill_shares(skill_id, resolved_user_ids)
    return {"shares": shares}


@router.get("/skills/{skill_id}/shares")
async def list_skill_shares_endpoint(
    skill_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """List users a skill is shared with. Only the creator can view this."""
    user_id = user["id"]

    # Verify skill exists and user is creator
    skill = await get_skill(skill_id)
    if not skill:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Skill not found"},
        )
    if skill["creator_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Only the skill creator can view shares"},
        )

    shares = await list_skill_shares(skill_id)
    return {"shares": shares}


@router.delete("/skills/{skill_id}/shares/{target_user_id}")
async def remove_skill_share_endpoint(
    skill_id: str,
    target_user_id: int,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Remove a user's share access to a skill. Only the creator can manage shares."""
    user_id = user["id"]

    # Verify skill exists and user is creator
    skill = await get_skill(skill_id)
    if not skill:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Skill not found"},
        )
    if skill["creator_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Only the skill creator can manage shares"},
        )

    removed = await remove_skill_share(skill_id, target_user_id)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Share not found"},
        )

    return {"success": True}


# ---------------------------------------------------------------------------
# User search endpoint (used by skill sharing type-ahead)
# ---------------------------------------------------------------------------


@router.get("/users/search")
async def search_users_endpoint(
    q: str = Query(..., min_length=2),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Search users by name or email for the sharing type-ahead."""
    from db.user_store import search_users

    users = await search_users(q, exclude_user_id=user["id"])
    return {"users": users}
