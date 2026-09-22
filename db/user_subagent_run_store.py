"""Data-access helpers for the user_subagent_runs table.

Each row tracks one cross-user subagent run: the caller's approved
run_user_subagent action request, the subagent conversation created in the
target user's account, and the caller-side wait handle that resolves when
the target user approves or denies the subagent's return call. The row is
the durable lifecycle record consulted by chat/user_subagent.py and the
return_to_caller dispatch arm.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from db.engine import AsyncSessionLocal
from db.models import UserSubagentRun, UserSubagentRunStatus

# Statuses in which the run accepts no further return calls.
TERMINAL_STATUSES = frozenset({
    UserSubagentRunStatus.RETURNED,
    UserSubagentRunStatus.DENIED,
    UserSubagentRunStatus.FAILED,
})


def _row_to_dict(row: UserSubagentRun) -> dict:
    return {
        "id": row.id,
        "caller_user_id": row.caller_user_id,
        "target_user_id": row.target_user_id,
        "caller_conversation_id": row.caller_conversation_id,
        "subagent_conversation_id": row.subagent_conversation_id,
        "wait_handle_id": row.wait_handle_id,
        "prompt": row.prompt,
        "skill_ids": row.skill_ids or [],
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


async def create_run(
    caller_user_id: int,
    target_user_id: int,
    caller_conversation_id: str,
    subagent_conversation_id: str,
    wait_handle_id: str,
    prompt: str,
    skill_ids: list[str],
) -> dict:
    """Insert a new run in status ``running`` and return the dict."""
    async with AsyncSessionLocal() as db:
        row = UserSubagentRun(
            id=str(uuid.uuid4()),
            caller_user_id=caller_user_id,
            target_user_id=target_user_id,
            caller_conversation_id=caller_conversation_id,
            subagent_conversation_id=subagent_conversation_id,
            wait_handle_id=wait_handle_id,
            prompt=prompt,
            skill_ids=list(skill_ids),
            status=UserSubagentRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _row_to_dict(row)


async def get_run(run_id: str) -> Optional[dict]:
    async with AsyncSessionLocal() as db:
        row = await db.get(UserSubagentRun, run_id)
        return _row_to_dict(row) if row else None


async def get_run_by_subagent_conversation(
    subagent_conversation_id: str,
) -> Optional[dict]:
    """Fetch the run owning a subagent conversation (at most one exists)."""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(UserSubagentRun)
            .where(
                UserSubagentRun.subagent_conversation_id
                == subagent_conversation_id
            )
            .order_by(UserSubagentRun.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.scalars().first()
        return _row_to_dict(row) if row else None


async def update_run_status(
    run_id: str,
    status: str,
    error: Optional[str] = None,
) -> Optional[dict]:
    """Set the run's status (stamping resolved_at on terminal states).

    First-write-wins for terminal states: once a run is in a terminal
    status, further transitions are ignored and the stored row is
    returned unchanged, so racing finalizers (deny vs. failure) cannot
    overwrite each other.
    """
    async with AsyncSessionLocal() as db:
        row = await db.get(UserSubagentRun, run_id)
        if row is None:
            return None
        if row.status in TERMINAL_STATUSES:
            return _row_to_dict(row)
        row.status = status
        if error is not None:
            row.error = error
        if status in TERMINAL_STATUSES:
            row.resolved_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
        return _row_to_dict(row)
