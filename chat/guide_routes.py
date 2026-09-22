"""REST API endpoints for guide CRUD.

Guides are deprecated in favor of skills and the whole surface sits behind
the admin ``guides`` feature gate (config/feature_gates.py): every route
here 403s with ``guides_disabled`` while the gate is closed for the
requesting user. The rows are never deleted by the gate, so an admin can
reopen it to let stragglers finish converting.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from chat.auth import get_current_user_cookie_or_apikey_checked
from config.feature_gates import guides_enabled_for
from db.guide_store import (
    get_guide,
    list_guides,
    update_guide,
    delete_guide,
)
from db.skill_store import (
    MAX_SKILL_NAME_LENGTH,
    create_skill,
    set_user_skill_autoload,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/app/api",
    tags=["guides"],
)


class UpdateGuideRequest(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None


def _require_guides_enabled(user: dict) -> None:
    """Reject the request unless the guides feature gate is open for this user."""
    if not guides_enabled_for(user["email"]):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "guides_disabled",
                "message": (
                    "Guides are disabled on this server. Use Skills instead, "
                    "or ask an admin to enable Guides in Settings > Features."
                ),
            },
        )


@router.get("/guides")
async def list_user_guides(
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """List all guides for the authenticated user.

    Returns default guide first, then others alphabetically. A missing
    default guide is no longer auto-created: guides are deprecated in
    favor of skills, so once converted (deleted) they must stay gone.
    """
    _require_guides_enabled(user)
    guides = await list_guides(user["id"])
    return {"guides": guides}


@router.post("/guides")
async def create_user_guide(
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Guide creation is disabled: skills replace guides."""
    _require_guides_enabled(user)
    raise HTTPException(
        status_code=410,
        detail={
            "error": "guides_deprecated",
            "message": "Guides have been replaced by Skills. Create a skill in the Skills section instead.",
        },
    )


@router.post("/guides/{guide_id}/convert-to-skill")
async def convert_guide_to_skill(
    guide_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Convert a guide into a private skill, then delete the guide.

    The new skill copies the guide's name and content. Converting the
    default guide additionally enables user-level auto-load on the new
    skill so its instructions keep applying to every conversation, then
    deletes the default guide (migration away from guides).
    """
    _require_guides_enabled(user)
    user_id = user["id"]

    guide = await get_guide(user_id, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Guide not found"})

    content = (guide.get("content") or "").strip()
    if not content:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "empty_guide",
                "message": "This guide has no content to convert. Delete it instead.",
            },
        )

    # Try the guide's own name first; on collision with an existing skill,
    # retry with numbered "(converted)" suffixes.
    base_name = guide["name"]
    candidates = [base_name] + [
        f"{base_name} (converted{'' if i == 1 else f' {i}'})" for i in range(1, 6)
    ]
    skill = None
    last_error = None
    for candidate in candidates:
        try:
            skill = await create_skill(
                creator_id=user_id,
                name=candidate[:MAX_SKILL_NAME_LENGTH],
                description=f"Converted from the '{base_name}' guide.",
                content=guide["content"],
                visibility="private",
            )
            break
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(e)})
        except Exception as e:
            error_msg = str(e)
            if "UNIQUE constraint failed" in error_msg or "unique" in error_msg.lower():
                last_error = e
                continue
            raise HTTPException(status_code=400, detail={"error": "convert_failed", "message": error_msg})

    if skill is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "duplicate_name",
                "message": f"A skill named '{base_name}' already exists. Rename the guide and try again.",
            },
        ) from last_error

    autoload_enabled = bool(guide.get("is_default"))
    if autoload_enabled:
        await set_user_skill_autoload(user_id, skill["id"], True)

    await delete_guide(user_id, guide_id)

    logger.info(
        "Converted guide %s to skill %s for user %s (autoload=%s)",
        guide_id, skill["id"], user_id, autoload_enabled,
    )
    return {"skill": skill, "autoload_enabled": autoload_enabled}


@router.get("/guides/{guide_id}")
async def get_user_guide(
    guide_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Get a specific guide by ID."""
    _require_guides_enabled(user)
    guide = await get_guide(user["id"], guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Guide not found"})
    return guide


@router.put("/guides/{guide_id}")
async def update_user_guide(
    guide_id: str,
    body: UpdateGuideRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Update a guide's name and/or content.

    After updating content, invalidates cached chat sessions so the
    new system prompt takes effect on the next message.
    """
    _require_guides_enabled(user)
    user_id = user["id"]

    try:
        guide = await update_guide(user_id, guide_id, name=body.name, content=body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "update_failed", "message": str(e)})
    except Exception as e:
        error_msg = str(e)
        if "UNIQUE constraint failed" in error_msg or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=409,
                detail={"error": "duplicate_name", "message": f"A guide with that name already exists"},
            )
        raise HTTPException(status_code=400, detail={"error": "update_failed", "message": error_msg})

    if not guide:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Guide not found"})

    # Invalidate cached chat sessions when content changes
    if body.content is not None:
        from chat.gemini_api import invalidate_user_sessions
        invalidate_user_sessions(user_id)

    return guide


@router.delete("/guides/{guide_id}")
async def delete_user_guide(
    guide_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Delete a guide (including the default guide -- guides are deprecated)."""
    _require_guides_enabled(user)
    deleted = await delete_guide(user["id"], guide_id)

    if not deleted:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Guide not found"})

    return {"success": True}
