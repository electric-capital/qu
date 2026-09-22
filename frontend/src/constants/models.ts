export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  maxInputTokens: number;
  /**
   * Deprecated models stay listed here so existing conversations/routines
   * keep resolving display names, providers, and context sizes, but they are
   * excluded from SELECTABLE_MODELS (and from the server's available_models)
   * so they cannot be picked for new work. Mirrors the `deprecated` flag in
   * MODEL_REGISTRY (chat/llm/config.py).
   */
  deprecated?: boolean;
}

export const AVAILABLE_MODELS: ModelInfo[] = [
  { id: 'gemini-3.1-pro-preview', name: 'Gemini 3.1 Pro', provider: 'gemini', maxInputTokens: 1_000_000, deprecated: true },
  { id: 'gemini-3-flash-preview', name: 'Gemini 3 Flash', provider: 'gemini', maxInputTokens: 1_000_000, deprecated: true },
  { id: 'gemini-3.1-flash-lite-preview', name: 'Gemini 3.1 Flash-Lite', provider: 'gemini', maxInputTokens: 1_000_000, deprecated: true },
  { id: 'gemini-3.5-flash', name: 'Gemini 3.5 Flash', provider: 'gemini', maxInputTokens: 1_000_000, deprecated: true },
  { id: 'gemini-3.5-flash-lite', name: 'Gemini 3.5 Flash-Lite', provider: 'gemini', maxInputTokens: 1_000_000 },
  { id: 'gemini-3.6-flash', name: 'Gemini 3.6 Flash', provider: 'gemini', maxInputTokens: 1_000_000 },
  { id: 'gemini-3.7-flash', name: 'Gemini 3.7 Flash', provider: 'gemini', maxInputTokens: 1_000_000 },
  { id: 'gemini-3.8-flash', name: 'Gemini 3.8 Flash', provider: 'gemini', maxInputTokens: 1_000_000 },
  { id: 'claude-haiku-4.5', name: 'Claude Haiku 4.5', provider: 'anthropic', maxInputTokens: 200_000 },
  { id: 'claude-sonnet-4-6', name: 'Claude Sonnet 4.6', provider: 'anthropic', maxInputTokens: 200_000 },
  { id: 'claude-sonnet-5', name: 'Claude Sonnet 5', provider: 'anthropic', maxInputTokens: 1_000_000 },
  { id: 'claude-opus-4-6', name: 'Claude Opus 4.6', provider: 'anthropic', maxInputTokens: 200_000 },
  { id: 'claude-opus-4-7', name: 'Claude Opus 4.7', provider: 'anthropic', maxInputTokens: 200_000 },
  { id: 'claude-opus-4-8', name: 'Claude Opus 4.8', provider: 'anthropic', maxInputTokens: 1_000_000 },
  { id: 'claude-opus-5', name: 'Claude Opus 5', provider: 'anthropic', maxInputTokens: 1_000_000 },
  { id: 'claude-opus-5-5', name: 'Claude Opus 5.5', provider: 'anthropic', maxInputTokens: 1_000_000 },
  // OpenRouter-served models (mirrors the backend "openrouter" entries in
  // MODEL_REGISTRY, chat/llm/config.py); hidden from pickers unless the
  // server reports them credentialed via available_models.
  { id: 'deepseek/deepseek-v4-flash-0731', name: 'DeepSeek V4 Flash 0731', provider: 'openrouter', maxInputTokens: 1_310_720 },
  { id: 'qwen/qwen3.8-27b', name: 'Qwen3.8 27B', provider: 'openrouter', maxInputTokens: 1_000_000 },
];

/**
 * Models offerable for new selection: everything in AVAILABLE_MODELS except
 * deprecated entries. Use this for pickers (composer, routines, Slack default);
 * use AVAILABLE_MODELS for lookups so deprecated models still display properly
 * on conversations/routines that already use them.
 */
export const SELECTABLE_MODELS: ModelInfo[] = AVAILABLE_MODELS.filter((m) => !m.deprecated);

/**
 * Curated picks surfaced at the top level of the composer model menu, in
 * display order. Each maps a user-friendly speed/cost tier to a concrete
 * model; the full SELECTABLE_MODELS list lives behind the "All models"
 * submenu. Entries whose model is not credentialed (or filtered out by the
 * conversation's provider lock) are hidden by the menu, not disabled.
 */
export const RECOMMENDED_MODELS: { modelId: string; label: string; cost: string }[] = [
  { modelId: 'claude-opus-4-8', label: 'Smart', cost: '$$$' },
  { modelId: 'claude-sonnet-5', label: 'Faster', cost: '$$' },
  { modelId: 'gemini-3.8-flash', label: 'Fastest', cost: '$' },
];

/**
 * Fallback default conversation model when the per-user server-side default
 * (users.settings.default_model, surfaced on GET /me) is unset/unknown. Single
 * source of truth for the FE default; applied after reading /me. Opus 4.8.
 *
 * Only used directly when the credentialed-model list is unknown; otherwise go
 * through resolveFallbackModel so a server without Anthropic credentials falls
 * back to a credentialed (e.g. Gemini) model instead.
 */
export const DEFAULT_MODEL_ID = 'claude-opus-4-8';

/**
 * Pick the fallback default model given the server's credentialed-model list
 * (available_models from GET /app/api/config; null while unknown). Prefers
 * DEFAULT_MODEL_ID, but when its backend credentials are missing falls back to
 * the first credentialed model in SELECTABLE_MODELS order (e.g. Gemini 3.5
 * Flash-Lite on a Gemini-only local instance; never a deprecated model). With no
 * list yet -- or nothing credentialed at all (sending is disabled anyway) --
 * returns DEFAULT_MODEL_ID.
 */
export function resolveFallbackModel(availableIds: string[] | null): string {
  if (availableIds === null || availableIds.includes(DEFAULT_MODEL_ID)) {
    return DEFAULT_MODEL_ID;
  }
  const firstAvailable = SELECTABLE_MODELS.find((m) => availableIds.includes(m.id));
  return firstAvailable ? firstAvailable.id : DEFAULT_MODEL_ID;
}

/** Look up the provider for a model ID. */
export function getProviderForModel(modelId: string): string {
  return AVAILABLE_MODELS.find(m => m.id === modelId)?.provider ?? 'gemini';
}

/** Look up a human-readable display name for a model ID. */
export function getModelDisplayName(modelId: string): string {
  return AVAILABLE_MODELS.find(m => m.id === modelId)?.name ?? modelId;
}

/** True when the model ID is a known-but-deprecated entry. */
export function isDeprecatedModel(modelId: string): boolean {
  return AVAILABLE_MODELS.find((m) => m.id === modelId)?.deprecated === true;
}

/**
 * Deprecated model IDs that should be silently remapped to their replacement.
 * Used when loading old conversations or routines that reference retired models.
 */
export const DEPRECATED_MODEL_MAP: Record<string, string> = {
  'gemini-3-pro-preview': 'gemini-3.1-pro-preview',
};
