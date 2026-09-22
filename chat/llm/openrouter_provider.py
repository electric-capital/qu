"""OpenRouter provider implementation using the openai SDK.

OpenRouter (https://openrouter.ai) exposes many third-party models behind
one OpenAI-compatible chat-completions API, authenticated by a single API
key. The key is managed as an editable API-key inference provider
(``config/inference_providers.py``), so personal deployments can run
non-Vertex models with nothing but a key pasted into Settings >
Inference Providers.

Session history uses the OpenAI chat message format (plain dicts):
``{"role": "user"|"assistant"|"tool", "content": ...}`` with assistant
tool calls in ``message["tool_calls"]`` and each tool result as its own
``role="tool"`` message referencing ``tool_call_id``. The system prompt is
NOT part of the stored history -- it is prepended at the API-call boundary,
mirroring how the Anthropic provider keeps ``system`` out of ``messages``.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from chat.llm.base import LLMProvider, StreamEvent, UsageStats, ToolSpec
from chat.llm.tool_schemas import to_openai_tools

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Optional attribution headers OpenRouter uses for its app rankings; harmless
# to send and they identify this deployment's traffic in the OpenRouter
# dashboard.
_OPENROUTER_HEADERS = {"X-Title": "Quest"}


@dataclass
class OpenRouterSession:
    """Stateless session container for OpenRouter conversations.

    The chat-completions API is stateless (no persistent chat object), so
    the "session" is just a data container holding the messages history,
    system prompt, tool definitions, and model name -- same pattern as
    AnthropicSession.
    """
    model: str
    system_prompt: str
    tools: list[dict]
    messages: list[dict] = field(default_factory=list)
    last_usage: dict = field(default_factory=dict)


class OpenRouterProvider(LLMProvider):
    """LLMProvider implementation for OpenRouter's OpenAI-compatible API."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Return a cached AsyncOpenAI client pointed at OpenRouter.

        The API key comes from the inference-credential store (admin
        Settings > Inference Providers), with the usual legacy fallback.
        """
        if self._client is not None:
            return self._client

        from openai import AsyncOpenAI
        from config.inference_providers import effective_api_key

        api_key, _source = effective_api_key("openrouter")
        if not api_key:
            raise ValueError(
                "OpenRouter API key not configured. Add it in Settings > "
                "Inference Providers (admin only)."
            )
        self._client = AsyncOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            default_headers=_OPENROUTER_HEADERS,
        )
        return self._client

    def reset_cached_clients(self) -> None:
        """Drop the cached client so a newly saved API key takes effect."""
        self._client = None

    def _get_openrouter_model_id(self, model: str) -> str:
        """Resolve the OpenRouter model ID for a given registry model id.

        Registry keys are the OpenRouter ids verbatim today (e.g.
        ``deepseek/deepseek-v4-flash-0731``); ``openrouter_model_id`` is the
        override hook mirroring ``vertex_model_id``.
        """
        from chat.llm.config import MODEL_REGISTRY
        entry = MODEL_REGISTRY.get(model, {})
        return entry.get("openrouter_model_id", model)

    async def check_model_access(self, model: str) -> None:
        """Issue a minimal completion call to verify the model actually works.

        Used by the admin inference-provider health check: catches a revoked
        or unfunded key, a model id OpenRouter no longer serves, and quota
        exhaustion. max_tokens=1 keeps the cost negligible.
        """
        client = self._get_client()
        await client.chat.completions.create(
            model=self._get_openrouter_model_id(model),
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )

    def create_session(
        self,
        model: str,
        system_prompt: str,
        tools: list[ToolSpec],
        history: list | None = None,
    ) -> Any:
        """Create an OpenRouter session (stateless container)."""
        return OpenRouterSession(
            model=model,
            system_prompt=system_prompt,
            tools=to_openai_tools(tools),
            messages=list(history) if history else [],
        )

    async def send_message_stream(
        self,
        session: Any,
        message: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat-completions response as normalized StreamEvents.

        Text deltas are yielded live; tool calls stream as argument
        fragments keyed by index, so they are accumulated and emitted as
        complete tool_call events once the stream ends. The final usage
        chunk (``stream_options.include_usage``) is captured into
        ``session.last_usage``.
        """
        client = self._get_client()

        if isinstance(message, str):
            session.messages.append({"role": "user", "content": message})
        elif isinstance(message, list):
            # Tool results are already complete role-tagged messages
            # (role="tool" entries, optionally followed by user text).
            session.messages.extend(message)

        session.last_usage = {}

        from chat.llm.config import MODEL_REGISTRY
        entry = MODEL_REGISTRY.get(session.model, {})
        max_tokens = entry.get("max_output_tokens", 8192)

        api_kwargs: dict = {
            "model": self._get_openrouter_model_id(session.model),
            "max_tokens": max_tokens,
            "messages": (
                [{"role": "system", "content": session.system_prompt}]
                + session.messages
            ),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if session.tools:
            api_kwargs["tools"] = session.tools

        # Accumulators: text so far, and per-index partial tool calls
        # (OpenAI streams tool-call name/arguments as fragments keyed by
        # ``index``; the id arrives on the first fragment).
        accumulated_text = ""
        tool_calls_by_index: dict[int, dict] = {}

        def _finalized_tool_calls() -> list[dict]:
            calls = []
            for index in sorted(tool_calls_by_index):
                partial = tool_calls_by_index[index]
                calls.append({
                    "id": partial["id"],
                    "type": "function",
                    "function": {
                        "name": partial["name"],
                        "arguments": partial["arguments"] or "{}",
                    },
                })
            return calls

        def _append_assistant_message(include_tool_calls: bool) -> None:
            tool_calls = _finalized_tool_calls() if include_tool_calls else []
            if not accumulated_text and not tool_calls:
                return
            assistant_message: dict = {
                "role": "assistant",
                "content": accumulated_text or None,
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            session.messages.append(assistant_message)

        try:
            stream = await client.chat.completions.create(**api_kwargs)
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    self._capture_usage(session, usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    accumulated_text += delta.content
                    yield StreamEvent(type="text", text=delta.content)
                for tc_delta in delta.tool_calls or []:
                    index = tc_delta.index or 0
                    partial = tool_calls_by_index.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if tc_delta.id:
                        partial["id"] = tc_delta.id
                    function = tc_delta.function
                    if function is not None:
                        if function.name:
                            partial["name"] += function.name
                        if function.arguments:
                            partial["arguments"] += function.arguments
        except asyncio.CancelledError:
            # Flush partial text so save_history() captures it; in-progress
            # tool calls are dropped (incomplete argument JSON can never be
            # dispatched or replayed).
            _append_assistant_message(include_tool_calls=False)
            raise

        # Assign fallback ids to any tool call the provider streamed without
        # one (seen from some OpenRouter upstreams) -- tool results must
        # reference a non-empty tool_call_id.
        for partial in tool_calls_by_index.values():
            if not partial["id"]:
                partial["id"] = f"call_{uuid.uuid4().hex[:24]}"

        _append_assistant_message(include_tool_calls=True)

        for index in sorted(tool_calls_by_index):
            partial = tool_calls_by_index[index]
            try:
                tool_args = (
                    json.loads(partial["arguments"]) if partial["arguments"] else {}
                )
            except json.JSONDecodeError:
                tool_args = {}
            if not isinstance(tool_args, dict):
                tool_args = {}
            yield StreamEvent(
                type="tool_call",
                tool_name=partial["name"],
                tool_args=tool_args,
                tool_id=partial["id"],
            )

    @staticmethod
    def _capture_usage(session: Any, usage: Any) -> None:
        """Record the stream's usage object into session.last_usage.

        Flattens the two nested detail objects into the raw keys the
        analytics layer stores (``cached_prompt_tokens`` from
        ``prompt_tokens_details.cached_tokens``, ``reasoning_tokens`` from
        ``completion_tokens_details.reasoning_tokens``), skipping fields the
        provider did not populate -- same convention as the Gemini provider.
        """
        last = session.last_usage
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None)
            if value is not None:
                last[key] = value
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(prompt_details, "cached_tokens", None)
        if cached is not None:
            last["cached_prompt_tokens"] = cached
        completion_details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(completion_details, "reasoning_tokens", None)
        if reasoning is not None:
            last["reasoning_tokens"] = reasoning

    def format_tool_results(
        self,
        session: Any,
        tool_results: list[dict[str, Any]],
    ) -> Any:
        """Format tool results as a list of role="tool" chat messages.

        Returned list entries are complete messages (unlike Anthropic's
        content blocks); send_message_stream() extends the history with
        them directly. extra_parts (advisory text parts from
        make_text_part) are folded into the tool message as a content-part
        array; file parts are unsupported (upload_file returns None) and
        any None entries are skipped.
        """
        messages = []
        for tr in tool_results:
            result_content = tr["result"]
            extra_parts = [p for p in tr.get("extra_parts", []) if p is not None]
            if extra_parts:
                content: Any = [
                    {"type": "text", "text": result_content},
                    *extra_parts,
                ]
            else:
                content = result_content
            messages.append({
                "role": "tool",
                "tool_call_id": tr["tool_id"],
                "content": content,
            })
        return messages

    def append_user_text(self, formatted_results: Any, text: str) -> None:
        """Append a user message after the tool-result messages."""
        formatted_results.append({"role": "user", "content": text})

    def get_usage(self, session: Any) -> UsageStats:
        """Extract token usage from the last stream.

        ``prompt_tokens`` INCLUDES the cached portion
        (``prompt_tokens_details.cached_tokens`` is the cache-hit subset) --
        the Gemini-style convention, which the coalesced fields follow.
        """
        usage = getattr(session, "last_usage", {})
        raw_usage = {
            key: usage[key]
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cached_prompt_tokens",
                "reasoning_tokens",
            )
            if key in usage
        }
        return UsageStats(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cached_tokens=usage.get("cached_prompt_tokens", 0),
            raw_usage=raw_usage,
        )

    def save_history(self, session: Any) -> list[dict]:
        """Serialize session history (already plain JSON-safe dicts)."""
        return session.messages

    def load_history(self, data: list[dict]) -> list:
        """Deserialize saved history for session restoration."""
        return data

    def get_pending_tool_uses(self, session: Any) -> list[tuple[str, str]]:
        """Return pending tool calls on the last assistant message."""
        return [
            (tid, tname)
            for tid, tname, _args in self.get_pending_tool_use_args(session)
        ]

    def get_pending_tool_use_args(self, session: Any) -> list[tuple[str, str, dict]]:
        """Return pending tool calls with their parsed arguments."""
        messages = getattr(session, "messages", None) or []
        return self.get_pending_tool_use_args_from_history(list(messages))

    def get_pending_tool_use_args_from_history(
        self, history: list[dict],
    ) -> list[tuple[str, str, dict]]:
        """Inspect serialized history for unanswered tool calls.

        Tool results immediately follow their assistant message as
        ``role="tool"`` messages, so a dangling tool call can only live on
        the final message -- checking ``history[-1]`` is sufficient, same
        as the Anthropic provider.
        """
        if not history:
            return []
        try:
            last = history[-1]
            if not isinstance(last, dict) or last.get("role") != "assistant":
                return []
            pending: list[tuple[str, str, dict]] = []
            for tool_call in last.get("tool_calls") or []:
                if not isinstance(tool_call, dict) or not tool_call.get("id"):
                    continue
                function = tool_call.get("function") or {}
                raw_args = function.get("arguments")
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except (json.JSONDecodeError, TypeError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                pending.append(
                    (tool_call["id"], function.get("name", "") or "", args)
                )
            return pending
        except Exception:
            logger.debug(
                "Failed to inspect pending tool calls on OpenRouter history",
                exc_info=True,
            )
            return []

    def repair_session_history(self, session: Any) -> int:
        """Repair orphaned tool_calls / tool messages in place.

        The chat-completions API enforces the same pairing invariants as
        Anthropic: every assistant ``tool_calls`` entry must be answered by
        a following ``role="tool"`` message, and every tool message must
        reference a tool call from the preceding assistant message. A
        dangling tool call on the FINAL message is left alone -- that is the
        legitimate suspend shape the resume bucket closes.
        """
        messages = getattr(session, "messages", None)
        if not isinstance(messages, list) or len(messages) < 2:
            return 0
        repaired = 0

        def _tool_call_ids(msg: Any) -> set[str]:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                return set()
            return {
                tc["id"] for tc in msg.get("tool_calls") or []
                if isinstance(tc, dict) and tc.get("id")
            }

        def _interrupted_result(tool_id: str) -> dict:
            return {
                "role": "tool",
                "tool_call_id": tool_id,
                "content": json.dumps({
                    "status": "interrupted",
                    "note": (
                        "The previous tool call was interrupted before "
                        "completing. Retry if needed."
                    ),
                }),
            }

        # Pass 1: every tool call before the final message must be answered
        # by the immediately following tool messages; close missing ones
        # with synthetic "interrupted" results.
        i = 0
        while i < len(messages) - 1:
            missing = _tool_call_ids(messages[i])
            if missing:
                j = i + 1
                while j < len(messages):
                    msg = messages[j]
                    if not isinstance(msg, dict) or msg.get("role") != "tool":
                        break
                    missing.discard(msg.get("tool_call_id"))
                    j += 1
                if missing:
                    for offset, tool_id in enumerate(sorted(missing)):
                        messages.insert(i + 1 + offset, _interrupted_result(tool_id))
                    repaired += len(missing)
            i += 1

        # Pass 2: drop tool messages that do not answer a tool call from the
        # nearest preceding assistant message (including duplicates).
        idx = 0
        current_ids: set[str] = set()
        answered: set[str] = set()
        while idx < len(messages):
            msg = messages[idx]
            role = msg.get("role") if isinstance(msg, dict) else None
            if role == "assistant":
                current_ids = _tool_call_ids(msg)
                answered = set()
            elif role == "tool":
                tool_id = msg.get("tool_call_id")
                if tool_id not in current_ids or tool_id in answered:
                    messages.pop(idx)
                    repaired += 1
                    continue
                answered.add(tool_id)
            else:
                current_ids = set()
                answered = set()
            idx += 1

        return repaired

    async def upload_file(
        self,
        file_path: str,
        mime_type: str,
        display_name: str = "",
        model: str = "",
    ) -> Any | None:
        """File attachments are not supported; always returns None.

        The tool-handler fallback path (inline text extraction / structured
        errors) covers unsupported-attachment messaging.
        """
        return None

    def make_file_part(self, file_ref: Any) -> Any:
        """Pass-through (upload_file never produces a ref)."""
        return file_ref

    def make_text_part(self, text: str) -> Any:
        """Create an OpenAI text content part dict."""
        return {"type": "text", "text": text}

    def inject_turn_warning(self, formatted_results: Any, warning_text: str) -> None:
        """Append a user-text warning message to formatted tool results."""
        formatted_results.append({"role": "user", "content": warning_text})
