"""REST API endpoints for user memories."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from chat.auth import get_current_user_cookie_or_apikey_checked
from db.memory_store import (
    MAX_MEMORY_SIZE,
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    search_memories,
    update_memory,
    archive_memory,
    unarchive_memory,
)

logger = logging.getLogger(__name__)

# Create APIRouter for memory endpoints
router = APIRouter(
    prefix="/app/api",
    tags=["memories"],
)


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class CreateMemoryRequest(BaseModel):
    """Request body for creating a memory."""
    content: str

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Memory content cannot be empty.")
        return v

    @field_validator("content")
    @classmethod
    def content_max_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_MEMORY_SIZE:
            raise ValueError(
                f"Memory content exceeds maximum size of {MAX_MEMORY_SIZE} bytes."
            )
        return v


class UpdateMemoryRequest(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Memory content cannot be empty.")
        return v

    @field_validator("content")
    @classmethod
    def content_max_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_MEMORY_SIZE:
            raise ValueError(
                f"Memory content exceeds maximum size of {MAX_MEMORY_SIZE} bytes."
            )
        return v


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/memories")
async def list_user_memories(
    q: Optional[str] = Query(default=None, description="FTS5 search query"),
    include_archived: bool = Query(default=False, description="Include archived memories"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    user_id = user["id"]

    if q:
        try:
            results = await search_memories(user_id, q)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_search_query",
                    "message": f"Invalid search query: {e}",
                },
            )
    else:
        results = await list_memories(user_id, include_archived=include_archived)

    return {"memories": results}


@router.post("/memories", status_code=201)
async def create_user_memory(
    body: CreateMemoryRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Create a new memory for the authenticated user.

    Args:
        body: Request body with memory content.
        user: Authenticated user dictionary.

    Returns:
        The created memory dict.
    """
    user_id = user["id"]

    try:
        memory = await create_memory(user_id, body.content)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_memory",
                "message": str(e),
            },
        )

    logger.info(
        "[memory] Created memory %s for user_id %s (%d bytes)",
        memory["id"],
        user_id,
        len(body.content.encode("utf-8")),
    )

    return memory


@router.get("/memories/{memory_id}")
async def get_user_memory(
    memory_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Get a specific memory by ID.

    Args:
        memory_id: UUID of the memory.
        user: Authenticated user dictionary.

    Returns:
        Memory dict.

    Raises:
        HTTPException: 404 if memory not found.
    """
    user_id = user["id"]
    memory = await get_memory(user_id, memory_id)

    if not memory:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "memory_not_found",
                "message": f"Memory {memory_id} not found.",
            },
        )

    return memory


@router.delete("/memories/{memory_id}")
async def delete_user_memory(
    memory_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Delete a specific memory by ID.

    Args:
        memory_id: UUID of the memory.
        user: Authenticated user dictionary.

    Returns:
        Success confirmation.

    Raises:
        HTTPException: 404 if memory not found.
    """
    user_id = user["id"]
    deleted = await delete_memory(user_id, memory_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "memory_not_found",
                "message": f"Memory {memory_id} not found.",
            },
        )

    logger.info("[memory] Deleted memory %s for user_id %s", memory_id, user_id)
    return {"success": True}


@router.put("/memories/{memory_id}")
async def update_user_memory(
    memory_id: str,
    body: UpdateMemoryRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    user_id = user["id"]

    try:
        memory = await update_memory(user_id, memory_id, body.content)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_memory",
                "message": str(e),
            },
        )

    if not memory:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "memory_not_found",
                "message": f"Memory {memory_id} not found.",
            },
        )

    logger.info(
        "[memory] Updated memory %s for user_id %s (%d bytes)",
        memory_id,
        user_id,
        len(body.content.encode("utf-8")),
    )
    return memory


@router.put("/memories/{memory_id}/archive")
async def archive_user_memory(
    memory_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    user_id = user["id"]
    memory = await archive_memory(user_id, memory_id)

    if not memory:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "memory_not_found",
                "message": f"Memory {memory_id} not found.",
            },
        )

    logger.info("[memory] Archived memory %s for user_id %s", memory_id, user_id)
    return memory


@router.put("/memories/{memory_id}/unarchive")
async def unarchive_user_memory(
    memory_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    user_id = user["id"]
    memory = await unarchive_memory(user_id, memory_id)

    if not memory:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "memory_not_found",
                "message": f"Memory {memory_id} not found.",
            },
        )

    logger.info("[memory] Unarchived memory %s for user_id %s", memory_id, user_id)
    return memory
