"""CreateMemoryHandler -- approve-to-save action request for user memories.

This handler replaces the old `suggest_memory` tool. The model issues a
`create_action_request(request_type="create_memory", params={"content": "..."}, ...)`
call which appends an inline approval card to the chat. On approve, the
content is persisted via `db.memory_store.create_memory`. On deny, no row
is created. The user-facing UI is identical to every other action request
(Approve / Revise / Deny).
"""

import logging

from db.models import ActionRequestType
from db.memory_store import MAX_MEMORY_SIZE, create_memory
from chat.action_request_types.base import ActionRequestHandler
from chat.action_request_types._param_validation import reject_unknown_params

logger = logging.getLogger(__name__)


_ALLOWED_PARAMS = frozenset({"content"})


class CreateMemoryHandler(ActionRequestHandler):
    """Save a new user memory after explicit user approval.

    Params:
        content (str): Memory text. Non-empty after stripping; max 4 KB
            (matches MAX_MEMORY_SIZE in db/memory_store.py).
    """

    @property
    def type_name(self) -> ActionRequestType:
        return ActionRequestType.CREATE_MEMORY

    @property
    def display_name(self) -> str:
        return "Save Memory"

    @property
    def approve_label(self) -> str:
        return "Save"

    @property
    def preview_fields(self) -> list[str]:
        return ["content"]

    async def render_preview(self, params: dict, user: dict | None = None) -> list[dict]:
        content = params.get("content", "")
        if not content:
            return []
        return [{"key": "Memory", "value": str(content)}]

    def validate_params(self, params: dict) -> dict:
        reject_unknown_params(self.type_name.value, params, _ALLOWED_PARAMS)

        content = params.get("content")
        if content is None:
            raise ValueError("Missing required parameter: content")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            raise ValueError("Memory content cannot be empty.")
        if len(content.encode("utf-8")) > MAX_MEMORY_SIZE:
            raise ValueError(
                f"Memory content exceeds maximum size of {MAX_MEMORY_SIZE} bytes."
            )
        return {"content": content}

    async def execute(
        self,
        params: dict,
        user: dict,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        memory = await create_memory(user["id"], params["content"])
        return {"success": True, "memory_id": memory["id"]}
