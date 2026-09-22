/**
 * Lightweight event bus for signaling request count changes.
 *
 * Components that create or resolve action requests call emitRequestCountChange()
 * to notify the RequestsBadge (in Sidebar.tsx) to re-fetch its count immediately,
 * rather than waiting for the next 30-second poll.
 *
 * This is deliberately outside the React context system to avoid cascading
 * re-renders (see commit 5241a25 for the render-isolation rationale).
 */

type Callback = () => void;

const listeners: Set<Callback> = new Set();

/**
 * Signal that the request count has changed.
 * Call this after creating or resolving an action request.
 */
export function emitRequestCountChange(): void {
  listeners.forEach((cb) => cb());
}

/**
 * Register a callback to be invoked when the request count changes.
 * Returns an unsubscribe function.
 */
export function onRequestCountChange(callback: Callback): () => void {
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
  };
}
