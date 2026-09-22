/**
 * GuidesReportTable - System Reports "Guides" section.
 *
 * Deprecation tracker for the retired guides feature: fetches
 * /admin/system-monitor/guides-report and lists every guide in the system
 * with its owner -- user guides from the guides table (including the empty
 * auto-created default rows) and project guides (projects with non-empty
 * "Project Instructions"). Fetch-on-demand like the Users section: loads on
 * mount and via the manual refresh button. A client-side "Hide empty"
 * toggle drops zero-length guides so real stragglers stand out.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { fetchAdminGuidesReport } from '../api/client';
import type { AdminGuideReportRow } from '../api/types';
import { formatRelativeTimestamp } from '../utils/formatters';
import './GuidesReportTable.css';

function formatChars(n: number): string {
  if (n === 0) return '';
  return n.toLocaleString();
}

export function GuidesReportTable() {
  const [rows, setRows] = useState<AdminGuideReportRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [hideEmpty, setHideEmpty] = useState(false);
  const mountedRef = useRef(true);
  // Monotonic fetch counter: a stale response must never overwrite the
  // result of a newer refresh.
  const fetchSeqRef = useRef(0);

  const load = useCallback(async () => {
    const seq = ++fetchSeqRef.current;
    setIsRefreshing(true);
    try {
      const resp = await fetchAdminGuidesReport();
      if (!mountedRef.current || seq !== fetchSeqRef.current) return;
      setRows(resp.guides);
      setError(null);
    } catch (err) {
      if (!mountedRef.current || seq !== fetchSeqRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mountedRef.current && seq === fetchSeqRef.current) {
        setIsRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    load();
    return () => {
      mountedRef.current = false;
    };
  }, [load]);

  const visibleRows = useMemo(() => {
    if (!rows) return [];
    return hideEmpty ? rows.filter((r) => r.content_length > 0) : rows;
  }, [rows, hideEmpty]);

  const summary = useMemo(() => {
    if (!rows) return null;
    const userGuides = visibleRows.filter((r) => r.kind === 'user').length;
    const projectGuides = visibleRows.length - userGuides;
    const owners = new Set(visibleRows.map((r) => r.user_id)).size;
    return { userGuides, projectGuides, owners, hidden: rows.length - visibleRows.length };
  }, [rows, visibleRows]);

  return (
    <div className="guides-report">
      <div className="guides-report-header">
        <h3>Guides</h3>
        <div className="header-actions">
          {summary && (
            <span
              className="guides-report-summary"
              title="User guides / project guides shown, and how many distinct owners they belong to"
            >
              {summary.userGuides} user, {summary.projectGuides} project &middot; {summary.owners}{' '}
              {summary.owners === 1 ? 'owner' : 'owners'}
              {summary.hidden > 0 ? ` (${summary.hidden} empty hidden)` : ''}
            </span>
          )}
          <label className="guides-report-toggle" title="Hide guides whose content is empty">
            <input
              type="checkbox"
              checked={hideEmpty}
              onChange={(e) => setHideEmpty(e.target.checked)}
            />
            Hide empty
          </label>
          <button
            type="button"
            className="refresh-button"
            title="Refresh now"
            aria-label="Refresh now"
            disabled={isRefreshing}
            onClick={() => load()}
          >
            <RefreshCw size={12} className={isRefreshing ? 'spinning' : undefined} />
          </button>
        </div>
      </div>
      {rows === null ? (
        <div className="guides-report-loading">Loading...</div>
      ) : visibleRows.length === 0 ? (
        <div className="guides-report-empty">
          {rows.length === 0 ? 'No guides in the system.' : 'No non-empty guides.'}
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th className="col-owner">Owner</th>
              <th className="col-kind">Kind</th>
              <th className="col-name">Name</th>
              <th className="col-size">Chars</th>
              <th className="col-routines">Routines</th>
              <th className="col-updated">Updated</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={`${row.kind}:${row.id}`}>
                <td
                  className="col-owner"
                  title={row.user_name ? `${row.user_name} <${row.user_email}>` : row.user_email}
                >
                  {row.user_name ? (
                    <>
                      <span className="owner-cell-name">{row.user_name}</span>
                      <span className="owner-cell-email">{row.user_email}</span>
                    </>
                  ) : (
                    <span className="owner-cell-name">{row.user_email}</span>
                  )}
                </td>
                <td className="col-kind">
                  {row.kind === 'user' ? (
                    <span className="kind-badge kind-user" title="Row in the guides table">
                      User guide
                    </span>
                  ) : (
                    <span
                      className="kind-badge kind-project"
                      title="Project with non-empty Project Instructions"
                    >
                      Project
                    </span>
                  )}
                </td>
                <td className="col-name" title={row.kind === 'project' ? `Project: ${row.name}` : row.name}>
                  <span className="guide-name">{row.name}</span>
                  {row.is_default && (
                    <span className="name-tag" title="The user's default guide">
                      default
                    </span>
                  )}
                  {row.public && (
                    <span className="name-tag" title="Public project">
                      public
                    </span>
                  )}
                </td>
                <td className="col-size" title="Length of the guide content in characters">
                  {row.content_length === 0 ? (
                    <span className="cell-empty" title="Empty content">
                      &mdash;
                    </span>
                  ) : (
                    formatChars(row.content_length)
                  )}
                </td>
                <td
                  className="col-routines"
                  title="Routines still using this guide as an override (user guides only)"
                >
                  {row.routine_count === null || row.routine_count === 0 ? (
                    <span className="cell-empty">&mdash;</span>
                  ) : (
                    row.routine_count
                  )}
                </td>
                <td className="col-updated" title={row.updated_at ?? row.created_at ?? undefined}>
                  {row.updated_at
                    ? formatRelativeTimestamp(row.updated_at)
                    : row.created_at
                      ? formatRelativeTimestamp(row.created_at)
                      : <span className="cell-empty">&mdash;</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <div className="guides-report-error">error: {error}</div>}
    </div>
  );
}
