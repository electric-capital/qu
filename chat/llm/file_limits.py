"""Per-backend attachment size limits and the model -> limit resolver.

Single source of truth for how many raw file bytes may be attached to a
single model request, per provider/backend. Used by the workspace-file
tool handlers for pre-flight checks (so an oversized file becomes a
normal tool error instead of an API-layer request failure that poisons
the conversation history) and by the providers as defense-in-depth.

Import direction: this module imports only from ``chat.llm.config`` so
both providers and the ``chat.gemini_api.tool_handlers`` package can import it
without cycles.
"""

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Derivation factors
# ---------------------------------------------------------------------------

# Files are base64-encoded into the request payload (Anthropic
# image/document blocks; Vertex-Gemini Part.from_bytes), inflating raw
# bytes by 4/3. So the raw-byte budget is payload_limit * 3/4.
BASE64_INFLATION_NUM = 3
BASE64_INFLATION_DEN = 4

# The rest of the request (system prompt, conversation history, tool
# definitions, other extra_parts) shares the same payload budget, so we
# reserve ~30% headroom for it.
ATTACH_SAFETY_FACTOR = 0.7


# ---------------------------------------------------------------------------
# Anthropic (Vertex AI) -- all Anthropic models in this repo are Vertex-backed
# ---------------------------------------------------------------------------

# Vertex AI limits Anthropic request payloads to 30 MB ("Agent Platform
# limits request payloads to 30 MB"; first-party API is 32 MB).
ANTHROPIC_VERTEX_MAX_REQUEST_BYTES = 30 * 1024 * 1024

# Effective per-file cap on raw file bytes:
#   30 MB * 3/4 (base64) * 0.7 (headroom) ~= 15.75 MB
ANTHROPIC_VERTEX_MAX_ATTACH_BYTES = int(
    ANTHROPIC_VERTEX_MAX_REQUEST_BYTES
    * BASE64_INFLATION_NUM / BASE64_INFLATION_DEN
    * ATTACH_SAFETY_FACTOR
)

# Anthropic additionally caps each image at 5 MB.
ANTHROPIC_IMAGE_MAX_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

# Gemini on Vertex inlines bytes as Part.from_bytes (the only Gemini
# backend; the genapi File API transport was removed). Vertex documents
# an inline request ceiling of ~20 MB total; 7 MB per part is a safe
# per-attachment cap that already leaves ample headroom for the rest of
# the request, so no extra base64 margin is applied.
GEMINI_VERTEX_MAX_ATTACH_BYTES = 7 * 1024 * 1024


# Conservative fallback for unknown/empty models: the smallest backend
# cap, so a model-threading bug can never reproduce the original
# oversized-payload blow-up.
_SMALLEST_ATTACH_BYTES = min(
    ANTHROPIC_VERTEX_MAX_ATTACH_BYTES,
    GEMINI_VERTEX_MAX_ATTACH_BYTES,
)


def get_attach_limit_for_model(model: str, mime_type: str = "") -> tuple[int, str]:
    """Return ``(limit_bytes, backend_label)`` for attaching a file to ``model``.

    ``limit_bytes`` is the maximum raw file size (strict ``>`` rejects) that
    may be attached to a single request for the given model's
    provider/backend. ``backend_label`` is a human-readable transport name
    for error messages.

    Unknown or empty model strings resolve to the smallest cap
    (conservative). Never raises -- this runs inside tool handlers.
    """
    from chat.llm.config import get_provider_for_model

    try:
        provider = get_provider_for_model(model)
    except Exception:
        provider = None

    mime_type = mime_type or ""

    if provider == "anthropic":
        limit = ANTHROPIC_VERTEX_MAX_ATTACH_BYTES
        if mime_type.startswith("image/"):
            limit = min(limit, ANTHROPIC_IMAGE_MAX_BYTES)
        return limit, "Anthropic on Vertex AI"

    if provider == "gemini":
        return GEMINI_VERTEX_MAX_ATTACH_BYTES, "Gemini (Vertex AI)"

    if provider == "openrouter":
        # The OpenRouter provider does not support binary attachments
        # (upload_file returns None; tool handlers fall back to inline
        # text). The smallest cap bounds the inline-text fallback path.
        limit = _SMALLEST_ATTACH_BYTES
        if mime_type.startswith("image/"):
            limit = min(limit, ANTHROPIC_IMAGE_MAX_BYTES)
        return limit, "OpenRouter"

    # Unknown model / provider: fall back to the smallest cap.
    if model:
        logger.debug(
            "[file_limits] unknown model %r; using smallest attach cap", model,
        )
    limit = _SMALLEST_ATTACH_BYTES
    if mime_type.startswith("image/"):
        limit = min(limit, ANTHROPIC_IMAGE_MAX_BYTES)
    return limit, "unknown model backend"
