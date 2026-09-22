/**
 * LatestActiveConversationsTable - System Reports "Latest Conversations"
 * section.
 *
 * Polls /admin/system-monitor/latest-active-conversations every 60s and
 * renders the global top-20 most-recently-active conversations. Pauses
 * polling while the tab is hidden; a manual refresh button re-fetches on
 * demand. A future iteration could replace the poll with an admin-scoped
 * WS event ("admin_activity_changed"); for v1 polling is fine.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { fetchLatestActiveConversations } from '../api/client';
import type { AdminActiveConversation } from '../api/types';
import { formatNumber, formatRelativeTimestamp } from '../utils/formatters';
import { ConversationUsageCell } from './ConversationUsageCell';
import './LatestActiveConversationsTable.css';

const POLL_INTERVAL_MS = 60_000;

// Title prefix marking conversations created by a routine (routine_id set --
// scheduled or one-click runs).
const ROUTINE_EMOJI = '⏰';

/**
 * Seconds-granularity elapsed-time label for the header ("5s ago", "2m ago",
 * "1h ago"). Distinct from formatRelativeTimestamp, which has minute
 * granularity ("just now") and is still used for the last-active column.
 */
function formatElapsed(sinceMs: number): string {
  const seconds = Math.max(0, Math.floor(sinceMs / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

export function LatestActiveConversationsTable() {
  const [rows, setRows] = useState<AdminActiveConversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [includeRoutines, setIncludeRoutines] = useState(true);
  // 1s ticker so the elapsed label re-renders as time passes.
  const [now, setNow] = useState(() => Date.now());
  const inFlightRef = useRef(false);
  const mountedRef = useRef(true);

  // `manual` bypasses the hidden-tab guard (a user clicking refresh is by
  // definition on a visible tab); interval-driven polls stay paused while
  // hidden. In-flight dedupe applies to both paths. Toggling the routines
  // filter recreates this callback, which re-runs the polling effect and
  // triggers an immediate re-fetch with the new filter.
  const load = useCallback(async (manual = false) => {
    if (inFlightRef.current || (!manual && document.hidden)) return;
    inFlightRef.current = true;
    setIsRefreshing(true);
    try {
      const resp = await fetchLatestActiveConversations(undefined, includeRoutines);
      if (!mountedRef.current) return;
      setRows(resp.conversations);
      setError(null);
      // Set only on success: after a failed poll the elapsed label keeps
      // growing while the error strip shows what went wrong (intended).
      setLastUpdated(new Date());
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      inFlightRef.current = false;
      if (mountedRef.current) setIsRefreshing(false);
    }
  }, [includeRoutines]);

  useEffect(() => {
    mountedRef.current = true;
    load();
    const interval = window.setInterval(() => load(), POLL_INTERVAL_MS);
    const onVisibilityChange = () => {
      if (!document.hidden) load();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      mountedRef.current = false;
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [load]);

  // Tick the elapsed label once a second (only once there is something to show).
  useEffect(() => {
    if (!lastUpdated) return;
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, [lastUpdated]);

  // The server already filters when includeRoutines is off; this client-side
  // pass makes the toggle take effect instantly on the rows we already have
  // (the refetch then restores a full-length list).
  const visibleRows =
    rows === null || includeRoutines
      ? rows
      : rows.filter((row) => !row.routine_id);

  return (
    <div className="latest-active-conversations">
      <div className="latest-active-conversations-header">
        <h3>Latest conversations</h3>
        <div className="header-actions">
          <label className="routines-toggle" title="Show conversations created by routines">
            <input
              type="checkbox"
              checked={includeRoutines}
              onChange={(e) => setIncludeRoutines(e.target.checked)}
            />
            Include Routines
          </label>
          {lastUpdated && (
            <span className="meta">
              Updated {formatElapsed(now - lastUpdated.getTime())}
            </span>
          )}
          <button
            type="button"
            className="refresh-button"
            title="Refresh now"
            aria-label="Refresh now"
            disabled={isRefreshing}
            onClick={() => load(true)}
          >
            <RefreshCw size={12} className={isRefreshing ? 'spinning' : undefined} />
          </button>
        </div>
      </div>
      {visibleRows === null ? (
        <div className="latest-active-conversations-loading">Loading...</div>
      ) : visibleRows.length === 0 ? (
        <div className="latest-active-conversations-empty">No active conversations.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th className="col-title">Conversation</th>
              <th className="col-user">User</th>
              <th className="col-tokens">Token usage</th>
              <th className="col-context">Context</th>
              <th className="col-active-days">Active days</th>
              <th className="col-model">Model</th>
              <th className="col-last-active">Last active</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={row.id}>
                <td className="col-title" title={row.title}>
                  {row.routine_id ? `${ROUTINE_EMOJI} ${row.title}` : row.title}
                </td>
                <td
                  className="col-user"
                  title={row.user_name ? `${row.user_name} <${row.user_email}>` : row.user_email}
                >
                  {row.user_name ? (
                    <>
                      <span className="user-cell-name">{row.user_name}</span>
                      <span className="user-cell-email">{row.user_email}</span>
                    </>
                  ) : (
                    <span className="user-cell-name">{row.user_email}</span>
                  )}
                </td>
                <td className="col-tokens">
                  <ConversationUsageCell
                    usageByModel={row.usage_by_model}
                    usageTotal={row.usage_total}
                  />
                </td>
                <td
                  className="col-context"
                  title="Input-side token count of the latest top-level model call -- what the next turn re-reads"
                >
                  {row.latest_context_tokens !== null ? (
                    formatNumber(row.latest_context_tokens)
                  ) : (
                    <span className="cell-empty">&mdash;</span>
                  )}
                </td>
                <td
                  className="col-active-days"
                  title="Distinct UTC days with at least one user message"
                >
                  {row.active_days}
                </td>
                <td
                  className="col-model"
                  title={row.last_model ?? undefined}
                >
                  {row.last_model ? (
                    row.last_model
                  ) : (
                    <span className="cell-empty">&mdash;</span>
                  )}
                </td>
                <td className="col-last-active">
                  {row.last_message_at ? formatRelativeTimestamp(row.last_message_at) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && (
        <div className="latest-active-conversations-error">error: {error}</div>
      )}
    </div>
  );
}
