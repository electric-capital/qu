"""Tests for per-session prompt-cache-write opt-out in the Anthropic provider.

One-off sessions (e.g. the compaction summarizer, chat/compaction.py) call
``disable_cache_writes(session)`` so their never-reused prompts don't pay
the cache-write surcharge. These tests assert the cache_control breakpoints
appear by default and vanish entirely when the session opts out.

Reuses the fake streaming client from tests/test_anthropic_thinking.py.
"""

import json

from chat.llm.anthropic_provider import AnthropicProvider
from tests.test_anthropic_thinking import (
    _BLOCK_STOP,
    _block_start,
    _delta,
    _message_start,
    _run_stream,
)

_TEXT_EVENTS = [
    _message_start(),
    _block_start("text"),
    _delta("text_delta", text="ok"),
    _BLOCK_STOP,
]

_TOOL_SPEC = [{
    "name": "get_current_time",
    "description": "d",
    "parameters": {"type": "object", "properties": {}},
}]


def test_cache_control_present_by_default():
    provider = AnthropicProvider()
    session = provider.create_session(
        model="claude-opus-4-8", system_prompt="sys", tools=_TOOL_SPEC,
    )
    _, fake = _run_stream(provider, session, "hello", _TEXT_EVENTS)

    kwargs = fake.captured_kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    # The (single) user message carries the conversation-prefix breakpoint.
    assert "cache_control" in json.dumps(kwargs["messages"])


def test_disable_cache_writes_removes_all_breakpoints():
    provider = AnthropicProvider()
    session = provider.create_session(
        model="claude-opus-4-8", system_prompt="sys", tools=_TOOL_SPEC,
    )
    provider.disable_cache_writes(session)
    _, fake = _run_stream(provider, session, "hello", _TEXT_EVENTS)

    kwargs = fake.captured_kwargs
    assert "cache_control" not in json.dumps(kwargs["system"])
    assert "cache_control" not in json.dumps(kwargs["tools"])
    assert "cache_control" not in json.dumps(kwargs["messages"])
    # The message itself still reaches the API untouched.
    assert kwargs["messages"][-1]["content"] == "hello"


def test_disable_cache_writes_without_tools():
    provider = AnthropicProvider()
    session = provider.create_session(
        model="claude-opus-4-8", system_prompt="sys", tools=[],
    )
    provider.disable_cache_writes(session)
    _, fake = _run_stream(provider, session, "summarize this", _TEXT_EVENTS)

    kwargs = fake.captured_kwargs
    assert "tools" not in kwargs
    assert "cache_control" not in json.dumps(kwargs["system"])
    assert "cache_control" not in json.dumps(kwargs["messages"])
