/**
 * Shared helper for priming the in-memory caches for a conversation we just
 * created on this tab, so ChatPanel paints the empty composer on the first
 * commit instead of stalling on "Loading conversation..." while
 * GET /conversations/<id> resolves.
 *
 * Extracted from Sidebar.seedNewConversation so both the Sidebar "New Chat"
 * flow and the root HomeComposer can seed identically.
 */

import { conversationStore } from '../store/conversationStore';
import { persistentWebSocket } from '../services/PersistentWebSocket';

/**
 * Seed the conversation store and adjacent caches for a freshly-created chat.
 *
 * - ``conversationStore.setMessages(id, [])`` lets ChatPanel paint the empty
 *   composer on the first commit instead of the loading placeholder.
 * - ``setLoadedSkills(id, [])`` lets useConversation skip the loaded-skills
 *   fetch (we know it's empty).
 * - ``persistentWebSocket.setLastSeq(id, 0)`` primes the WS subscribe so the
 *   server answers up_to_date.
 *
 * The fetch in useConversation still runs as a safety net only when this seed
 * is missing (i.e. first-load via URL, not just-created).
 *
 * @param conversationId the id returned by createConversation()
 * @param setLoadedSkills the context's setLoadedSkillsForConversation
 */
export function seedNewConversation(
  conversationId: string,
  setLoadedSkills: (conversationId: string, skillIds: string[]) => void,
): void {
  conversationStore.setMessages(conversationId, []);
  setLoadedSkills(conversationId, []);
  persistentWebSocket.setLastSeq(conversationId, 0);
}
