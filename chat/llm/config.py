"""Provider configuration, model registry, and credential loading.

Maps model IDs to providers and manages provider singleton instances.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

# All models (Gemini and Anthropic) run on Vertex AI, auth via ADC +
# project/region. The Gemini developer-endpoint ("genapi") transport was
# removed: thought signatures are not portable across the Vertex / AI
# Studio border, so mixing backends in one conversation history broke
# mid-conversation model switches with signature-validation errors.
# ``vertex_model_id`` is the publisher id used on Vertex (defaults to the
# registry key when absent). Used by both Gemini-on-Vertex and Anthropic.
#
# ``deprecated`` marks a model that stays fully runnable (existing
# conversations, routines, and stored defaults keep working) but is excluded
# from get_available_models() so it disappears from the new-conversation
# picker. The FE mirrors the flag in frontend/src/constants/models.ts.
#
# ``thinking_effort`` (Anthropic only) turns on adaptive thinking for the
# model and sets the ``output_config.effort`` level sent with every request
# ("low" | "medium" | "high" | "xhigh" | "max"). Models without the key run
# with thinking off (the pre-4.6-era behavior: omitting the ``thinking``
# param disables it on Opus 4.7/4.8). Keep the value constant for a model:
# effort shapes the rendered prompt, so changing it between requests of one
# conversation invalidates the Anthropic prompt cache.
MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "gemini-3.1-pro-preview": {
        "provider": "gemini",
        "backend": "vertex",
        "display_name": "Gemini 3.1 Pro",
        "vertex_model_id": "gemini-3.1-pro-preview",
        "deprecated": True,
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 65_000,
    },
    "gemini-3-flash-preview": {
        "provider": "gemini",
        "backend": "vertex",
        "display_name": "Gemini 3 Flash",
        "vertex_model_id": "gemini-3-flash-preview",
        "deprecated": True,
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 65_000,
    },
    "gemini-3.1-flash-lite-preview": {
        "provider": "gemini",
        "backend": "vertex",
        "display_name": "Gemini 3.1 Flash-Lite",
        # Historically ran on the genapi backend under the ``-preview`` id;
        # the Vertex publisher catalog serves the same model without the
        # suffix (the ``-preview`` id 404s on Vertex).
        "vertex_model_id": "gemini-3.1-flash-lite",
        "deprecated": True,
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 65_000,
    },
    "gemini-3.5-flash": {
        "provider": "gemini",
        "backend": "vertex",
        "display_name": "Gemini 3.5 Flash",
        "vertex_model_id": "gemini-3.5-flash",
        "deprecated": True,
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 64_000,
    },
    "gemini-3.5-flash-lite": {
        "provider": "gemini",
        "backend": "vertex",
        "display_name": "Gemini 3.5 Flash-Lite",
        "vertex_model_id": "gemini-3.5-flash-lite",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 64_000,
    },
    "gemini-3.6-flash": {
        "provider": "gemini",
        "backend": "vertex",
        "display_name": "Gemini 3.6 Flash",
        "vertex_model_id": "gemini-3.6-flash",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 65_000,
    },
    "gemini-3.7-flash": {
        "provider": "gemini",
        "backend": "vertex",
        "display_name": "Gemini 3.7 Flash",
        "vertex_model_id": "gemini-3.7-flash",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 65_000,
    },
    # Released 2026-09-02 (1,048,576-token input window, 65,536 output,
    # thinking low/medium/high -- ``minimal`` is rejected). Limits kept in
    # line with the other Flash entries.
    "gemini-3.8-flash": {
        "provider": "gemini",
        "backend": "vertex",
        "display_name": "Gemini 3.8 Flash",
        "vertex_model_id": "gemini-3.8-flash",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 65_000,
    },
    "claude-haiku-4.5": {
        "provider": "anthropic",
        "display_name": "Claude Haiku 4.5",
        "vertex_model_id": "claude-haiku-4-5",
        "max_input_tokens": 200_000,
        "max_output_tokens": 8_192,
    },
    "claude-sonnet-4-6": {
        "provider": "anthropic",
        "display_name": "Claude Sonnet 4.6",
        "vertex_model_id": "claude-sonnet-4-6",
        "max_input_tokens": 200_000,
        "max_output_tokens": 8_192,
    },
    "claude-opus-4-6": {
        "provider": "anthropic",
        "display_name": "Claude Opus 4.6",
        "vertex_model_id": "claude-opus-4-6",
        "max_input_tokens": 200_000,
        "max_output_tokens": 16_384,
    },
    "claude-opus-4-7": {
        "provider": "anthropic",
        "display_name": "Claude Opus 4.7",
        "vertex_model_id": "claude-opus-4-7",
        "vertex_region": "global",
        "max_input_tokens": 200_000,
        "max_output_tokens": 16_384,
    },
    "claude-opus-4-8": {
        "provider": "anthropic",
        "display_name": "Claude Opus 4.8",
        "vertex_model_id": "claude-opus-4-8",
        "vertex_region": "global",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "thinking_effort": "medium",
    },
    "claude-sonnet-5": {
        "provider": "anthropic",
        "display_name": "Claude Sonnet 5",
        "vertex_model_id": "claude-sonnet-5",
        "vertex_region": "global",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 128_000,
    },
    "claude-opus-5": {
        "provider": "anthropic",
        "display_name": "Claude Opus 5",
        "vertex_model_id": "claude-opus-5",
        "vertex_region": "global",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        # Opus 5 ships with elevated safety classifiers that can decline a
        # request (HTTP 200, stop_reason "refusal") -- benign security work
        # occasionally trips them. Requests refused this way are retried on
        # these models, in order, via the Anthropic SDK's client-side
        # refusal-fallback middleware (Vertex has no server-side `fallbacks`
        # param). Opus 4.8 is Anthropic's recommended fallback for
        # cyber-category refusals. Entries must be Anthropic registry models.
        "refusal_fallback_models": ["claude-opus-4-8"],
    },
    # Released 2026-09-22. Same 1M/128K limits as Opus 5 at a lower list
    # price ($4/$20 per 1M tokens). Adaptive thinking is ALWAYS on for this
    # model -- ``thinking: disabled`` and manual ``budget_tokens`` both 400
    # -- so ``thinking_effort`` is set explicitly (the model's own default
    # is "medium"; Opus 5's was "high") rather than left to the API. Forced
    # tool use (``tool_choice`` any/tool) is rejected; the provider only
    # ever uses the default auto choice. Text the model writes between tool
    # calls arrives as progress-update ``thinking`` blocks (empty at the
    # default display), so between-tool-call narration is not streamed to
    # the UI on this model. Its thinking blocks can only be read back by
    # itself (and Fable/Mythos 5.1 on the Claude API): a refusal fallback or
    # mid-conversation switch to another model runs without them (dropped
    # server-side, not an error).
    "claude-opus-5-5": {
        "provider": "anthropic",
        "display_name": "Claude Opus 5.5",
        "vertex_model_id": "claude-opus-5-5",
        "vertex_region": "global",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "thinking_effort": "medium",
        # Broader safety classifiers than Opus 5 (cyber + bio +
        # reasoning_extraction). Same client-side refusal-fallback chain.
        "refusal_fallback_models": ["claude-opus-4-8"],
    },
    # OpenRouter-served models (provider "openrouter", backend "openrouter"):
    # the curated set Quest supports on OpenRouter for personal deployments.
    # Registry keys are the OpenRouter model ids verbatim; an
    # ``openrouter_model_id`` entry can override the wire id if the two ever
    # need to diverge (mirroring ``vertex_model_id``). Configured-ness comes
    # from the OpenRouter API key in the inference-credential store, not from
    # Vertex project config.
    # Context/output limits from OpenRouter's model catalog
    # (context_length 1,310,720; max_completion_tokens 384,000 -- capped
    # lower here in line with the other registry entries).
    "deepseek/deepseek-v4-flash-0731": {
        "provider": "openrouter",
        "backend": "openrouter",
        # The date suffix is DeepSeek's snapshot version -- keep it in the
        # display name so users can tell snapshots apart as more are added.
        "display_name": "DeepSeek V4 Flash 0731",
        "max_input_tokens": 1_310_720,
        "max_output_tokens": 64_000,
    },
    # Context/output limits from OpenRouter's model catalog (context_length
    # 1,000,000; max_completion_tokens 131,072 -- capped lower in line with
    # the other registry entries).
    "qwen/qwen3.8-27b": {
        "provider": "openrouter",
        "backend": "openrouter",
        "display_name": "Qwen3.8 27B",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 64_000,
    },
}


def get_provider_for_model(model_id: str) -> str:
    """Return the provider name for a given model ID.

    Args:
        model_id: Model identifier string (e.g. 'gemini-3.1-pro-preview').

    Returns:
        Provider name string: 'gemini', 'anthropic', or 'openrouter'.

    Raises:
        ValueError: If the model ID is not in the registry.
    """
    entry = MODEL_REGISTRY.get(model_id)
    if not entry:
        raise ValueError(
            f"Unknown model: {model_id}. "
            f"Valid models: {list(MODEL_REGISTRY.keys())}"
        )
    return entry["provider"]


def get_backend_for_model(model_id: str) -> str | None:
    """Return the SDK transport backend for a given model ID.

    Used for cost analytics: the same logical model can be billed differently
    depending on the backend. Vertex-served models (all Gemini and Anthropic
    models; the Gemini ``"genapi"`` transport was removed) resolve to
    ``"vertex"``; OpenRouter-served models resolve to ``"openrouter"``.
    Historical analytics rows recorded under ``"genapi"`` keep that label in
    the DB.

    Returns ``None`` for unknown models so callers can record without raising.
    """
    entry = MODEL_REGISTRY.get(model_id)
    if not entry:
        return None
    if entry.get("provider") == "anthropic":
        return "vertex"
    return entry.get("backend", "vertex")


def get_configured_models() -> list[str]:
    """Return the model IDs whose backend credentials appear configured.

    This is a config-presence check, not a live probe:

    - Gemini models need ``gemini_vertex.vertex_project_id``.
    - Anthropic models need ``anthropic.vertex_project_id``.
    - OpenRouter models need an OpenRouter API key in the
      inference-credential store (Settings > Inference Providers).

    Models flagged ``deprecated`` in MODEL_REGISTRY are excluded regardless of
    credentials: they are still runnable (existing conversations/routines keep
    working) but must not be offered for new selection.

    This is the universe the health sweeps check (``run_startup_checks``);
    the picker goes through :func:`get_available_models`, which additionally
    drops models with a failing health verdict. Order matches MODEL_REGISTRY.
    """
    from config.inference_providers import effective_api_key
    from config.server_config import load_server_config

    server_config = load_server_config()
    gemini_vertex_ok = bool(server_config["gemini_vertex"]["vertex_project_id"])
    anthropic_ok = bool(server_config["anthropic"]["vertex_project_id"])
    openrouter_ok = bool(effective_api_key("openrouter")[0])

    configured = []
    for model_id, entry in MODEL_REGISTRY.items():
        if entry.get("deprecated"):
            continue
        provider = entry["provider"]
        if provider == "anthropic":
            if anthropic_ok:
                configured.append(model_id)
        elif provider == "openrouter":
            if openrouter_ok:
                configured.append(model_id)
        elif gemini_vertex_ok:
            configured.append(model_id)
    return configured


def get_available_models() -> list[str]:
    """Return the model IDs that are configured AND not known-unhealthy.

    Starts from :func:`get_configured_models` and drops every model whose
    latest verdict in the model-health store (``chat/llm/health.py``) is a
    failure. A model that was never checked -- e.g. right after boot while
    the startup sweep is still running, or when ``data/model_health.json``
    was deleted -- stays included: only a recorded failing check hides a
    model, so an absent verdict can never empty the picker.

    Health only filters *selection* (the frontend model picker via
    GET /app/api/config); existing conversations and routines on an
    unhealthy model keep running and fail at the provider, same as before.
    Recovery is automatic: the startup sweep, admin per-model rechecks, and
    the post-credential-save recheck all update the store, and this function
    re-reads it on every call.
    """
    from chat.llm.health import get_model_health_store

    statuses = get_model_health_store().get_all()
    return [
        model_id
        for model_id in get_configured_models()
        # Only an explicit failing verdict hides a model; a missing or
        # malformed entry keeps it visible.
        if (statuses.get(model_id) or {}).get("ok") is not False
    ]


# ---------------------------------------------------------------------------
# Provider singletons
# ---------------------------------------------------------------------------

_provider_instances: dict[str, Any] = {}


def get_provider_instance(provider_name: str) -> "LLMProvider":
    """Return a singleton LLMProvider instance for the given provider name.

    Lazily creates provider instances on first use.

    Args:
        provider_name: 'gemini', 'anthropic', or 'openrouter'.

    Returns:
        An LLMProvider implementation instance.

    Raises:
        ValueError: If the provider name is unknown.
    """

    if provider_name in _provider_instances:
        return _provider_instances[provider_name]

    if provider_name == "gemini":
        from chat.llm.gemini_provider import GeminiProvider
        instance = GeminiProvider()
        _provider_instances[provider_name] = instance
        return instance

    if provider_name == "anthropic":
        from chat.llm.anthropic_provider import AnthropicProvider
        instance = AnthropicProvider()
        _provider_instances[provider_name] = instance
        return instance

    if provider_name == "openrouter":
        from chat.llm.openrouter_provider import OpenRouterProvider
        instance = OpenRouterProvider()
        _provider_instances[provider_name] = instance
        return instance

    raise ValueError(
        f"Unknown provider: {provider_name}. "
        "Valid providers: gemini, anthropic, openrouter"
    )


def reset_provider_client_caches() -> None:
    """Drop cached SDK clients on providers that support it.

    Called when inference-provider credentials change through the admin UI so
    the new credentials take effect without a server restart. Providers opt in
    by implementing ``reset_cached_clients()``.
    """
    for instance in _provider_instances.values():
        reset = getattr(instance, "reset_cached_clients", None)
        if callable(reset):
            reset()


def get_provider_for_model_instance(model_id: str) -> "LLMProvider":
    """Convenience: return a provider instance for a model ID.

    Combines get_provider_for_model() and get_provider_instance().
    """
    provider_name = get_provider_for_model(model_id)
    return get_provider_instance(provider_name)
