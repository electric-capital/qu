// Per-conversation flags (opt-in behaviors set at the start of a conversation).
//
// This is a small, hand-mirrored copy of the server-side registry in
// chat/conversation_flags.py (KNOWN_FLAGS + FLAG_LABELS). Like the built-in model list
// in constants/models.ts, the FE keeps a static mirror rather than fetching the
// list, because the registry is tiny and server-controlled. Adding a future
// flag therefore requires touching BOTH chat/conversation_flags.py and this file.

export interface FlagDefinition {
  id: string;
  label: string;
  description: string;
  // Server-global feature gate key (config/feature_gates.py KNOWN_FEATURES).
  // When set, the flag is offered only while an admin has the feature on
  // (enabled_features from GET /app/api/me).
  feature?: string;
}

export const AVAILABLE_FLAGS: FlagDefinition[] = [
  {
    id: 'nested_subagents',
    label: 'Nested sub-agents',
    description:
      'Lets 1st-level sub-agents spawn their own 2nd-level sub-agents (restricted to Haiku / Gemini Flash Lite).',
  },
  {
    id: 'user_subagents',
    label: 'Cross-user subagents',
    description:
      "Lets this conversation propose running approved read-only subagents in other users' accounts.",
    feature: 'user_subagents',
  },
];

/** The flags the composer may offer given the server's enabled feature gates. */
export function getVisibleFlags(enabledFeatures: string[]): FlagDefinition[] {
  return AVAILABLE_FLAGS.filter(
    f => !f.feature || enabledFeatures.includes(f.feature),
  );
}

/** Look up a human-readable label for a flag ID (falls back to the raw id). */
export function getFlagLabel(flagId: string): string {
  return AVAILABLE_FLAGS.find(f => f.id === flagId)?.label ?? flagId;
}
