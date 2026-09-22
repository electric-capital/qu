"""Data-access helpers for the slack_conversations table.

Each row links a (slack_channel_id, slack_thread_ts) pair to a Quest
conversation, so the Socket Mode worker can route threaded replies to
the same conversation context.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from db.engine import AsyncSessionLocal
from db.models import SlackConversation


def _row_to_dict(row: SlackConversation) -> dict:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "user_id": row.user_id,
        "slack_channel_id": row.slack_channel_id,
        "slack_thread_ts": row.slack_thread_ts,
        "slack_user_id": row.slack_user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def create_slack_conversation(
    conversation_id: str,
    user_id: int,
    slack_channel_id: str,
    slack_thread_ts: str,
    slack_user_id: Optional[str] = None,
) -> dict:
    """Insert a new slack_conversations row."""
    async with AsyncSessionLocal() as db:
        row = SlackConversation(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            user_id=user_id,
            slack_channel_id=slack_channel_id,
            slack_thread_ts=slack_thread_ts,
            slack_user_id=slack_user_id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _row_to_dict(row)


async def get_slack_conversation(
    slack_channel_id: str, slack_thread_ts: str
) -> Optional[dict]:
    """Look up a slack_conversations row by (channel, thread_ts)."""
    async with AsyncSessionLocal() as db:
        stmt = select(SlackConversation).where(
            SlackConversation.slack_channel_id == slack_channel_id,
            SlackConversation.slack_thread_ts == slack_thread_ts,
        )
        result = await db.execute(stmt)
        row = result.scalars().first()
        return _row_to_dict(row) if row else None


async def get_slack_conversation_by_id(
    conversation_id: str,
) -> Optional[dict]:
    """Look up a slack_conversations row by Quest ``conversation_id``.

    Used by the headless resume path to recover the (channel, thread_ts)
    pair when a Slack-driven conversation suspended on
    ``send_slack_reply_and_get_response`` and is being resumed without
    the original Socket Mode handler in scope.
    """
    async with AsyncSessionLocal() as db:
        stmt = select(SlackConversation).where(
            SlackConversation.conversation_id == conversation_id,
        )
        result = await db.execute(stmt)
        row = result.scalars().first()
        return _row_to_dict(row) if row else None


async def list_slack_conversations_for_user(user_id: int) -> list[dict]:
    """Return all slack_conversations rows owned by a user."""
    async with AsyncSessionLocal() as db:
        stmt = select(SlackConversation).where(SlackConversation.user_id == user_id)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return [_row_to_dict(r) for r in rows]
