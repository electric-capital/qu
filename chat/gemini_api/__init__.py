"""Provider-agnostic conversation loop (Gemini, Anthropic, OpenRouter).

The package name is historical: it predates the multi-provider abstraction in chat/llm.
"""

from chat.gemini_api.conversation import run_conversation_turn
from chat.gemini_api.session import (
    remove_chat_session,
    invalidate_user_sessions,
    cancel_pending_wait_handles_for_conversation,
)
