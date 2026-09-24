"""Token-usage accumulation for the top-level conversation loop.

``run_conversation_turn`` runs a multi-turn loop where each turn produces
top-level provider usage and tool arms may merge in sub-agent usage
dicts. This module owns the accumulation and the final stats payload so
the loop never touches individual counters.
"""

from typing import Any

from chat.llm.base import compute_new_input_tokens, compute_total_context_tokens
from chat.llm.config import get_max_input_tokens


class UsageAccumulator:
    """Accumulates token usage across all turns of one run_conversation_turn run.

    Two inputs feed it:

    * ``add_turn`` -- a top-level model turn's ``UsageStats`` (from
      ``provider.get_usage``). The last one is retained so
      ``build_stats`` can compute the context-window indicator.
    * ``add_sub_agent`` -- the plain-dict ``usage_accumulator`` filled by
      the sub-agent runners (``_run_sub_agent`` and friends).

    ``build_stats`` folds both into the stats payload persisted as the
    turn's ``stats`` structured message and emitted over the WS.
    """

    def __init__(self) -> None:
        self.top_level_input_tokens = 0
        self.top_level_output_tokens = 0
        self.top_level_cached_tokens = 0
        self.top_level_cache_creation_tokens = 0
        self.top_level_cache_read_tokens = 0
        self.top_level_new_input_tokens = 0
        self.sub_agent_input_tokens = 0
        self.sub_agent_output_tokens = 0
        self.sub_agent_cached_tokens = 0
        self.sub_agent_cache_creation_tokens = 0
        self.sub_agent_cache_read_tokens = 0
        self.sub_agent_new_input_tokens = 0
        self.sub_agent_call_count = 0
        # Last top-level turn's raw UsageStats, for the context indicator.
        self.last_turn_usage: Any = None

    def add_turn(self, usage: Any, provider_name: str) -> None:
        """Accumulate one top-level model turn's ``UsageStats``."""
        self.last_turn_usage = usage
        self.top_level_input_tokens += usage.input_tokens
        self.top_level_output_tokens += usage.output_tokens
        self.top_level_cached_tokens += usage.cached_tokens
        self.top_level_cache_creation_tokens += usage.cache_creation_tokens
        self.top_level_cache_read_tokens += usage.cache_read_tokens
        self.top_level_new_input_tokens += compute_new_input_tokens(
            usage, provider_name,
        )

    def add_sub_agent(self, usage: dict) -> None:
        """Merge a sub-agent arm's ``usage_accumulator`` dict."""
        self.sub_agent_input_tokens += usage.get("input_tokens", 0)
        self.sub_agent_output_tokens += usage.get("output_tokens", 0)
        self.sub_agent_cached_tokens += usage.get("cached_tokens", 0)
        self.sub_agent_cache_creation_tokens += usage.get("cache_creation_tokens", 0)
        self.sub_agent_cache_read_tokens += usage.get("cache_read_tokens", 0)
        self.sub_agent_new_input_tokens += usage.get("new_input_tokens", 0)
        self.sub_agent_call_count += usage.get("call_count", 0)

    def build_stats(
        self,
        *,
        duration_ms: int,
        tool_calls: int,
        turns: int,
        provider_name: str,
        model: str,
    ) -> dict:
        """Build the stats payload for the end-of-run ``stats`` message.

        Context-window usage comes from the last top-level turn's raw
        usage (input-side size per the provider's counting rules); the
        window ceiling comes from the model registry.
        """
        context_tokens = 0
        if self.last_turn_usage is not None:
            context_tokens = compute_total_context_tokens(
                self.last_turn_usage, provider_name,
            )
        max_context_tokens = get_max_input_tokens(model)

        return {
            "input_tokens": self.top_level_input_tokens + self.sub_agent_input_tokens,
            "output_tokens": self.top_level_output_tokens + self.sub_agent_output_tokens,
            "cached_tokens": self.top_level_cached_tokens + self.sub_agent_cached_tokens,
            "duration_ms": duration_ms,
            "tool_calls": tool_calls,
            "turns": turns,
            # Unified new input tokens metric (accumulated across all turns)
            "new_input_tokens": (
                self.top_level_new_input_tokens + self.sub_agent_new_input_tokens
            ),
            "provider": provider_name,
            "model": model,
            "cache_creation_tokens": (
                self.top_level_cache_creation_tokens
                + self.sub_agent_cache_creation_tokens
            ),
            "cache_read_tokens": (
                self.top_level_cache_read_tokens + self.sub_agent_cache_read_tokens
            ),
            # Breakdown by call type
            "top_level_input_tokens": self.top_level_input_tokens,
            "top_level_output_tokens": self.top_level_output_tokens,
            "top_level_cached_tokens": self.top_level_cached_tokens,
            "top_level_new_input_tokens": self.top_level_new_input_tokens,
            "top_level_cache_creation_tokens": self.top_level_cache_creation_tokens,
            "top_level_cache_read_tokens": self.top_level_cache_read_tokens,
            "sub_agent_input_tokens": self.sub_agent_input_tokens,
            "sub_agent_output_tokens": self.sub_agent_output_tokens,
            "sub_agent_cached_tokens": self.sub_agent_cached_tokens,
            "sub_agent_new_input_tokens": self.sub_agent_new_input_tokens,
            "sub_agent_cache_creation_tokens": self.sub_agent_cache_creation_tokens,
            "sub_agent_cache_read_tokens": self.sub_agent_cache_read_tokens,
            "sub_agent_call_count": self.sub_agent_call_count,
            # Context window usage for the indicator
            "context_tokens": context_tokens,
            "max_context_tokens": max_context_tokens,
        }
