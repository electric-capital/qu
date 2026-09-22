/**
 * Modal for viewing table data from a project's SQLite database.
 * Follows the FileViewerModal pattern.
 */

import { useState, useEffect, useCallback } from 'react';
import { fetchTableData, ProjectDbApiError } from '../api/projectDbApi';
import type { SortDirection, TableDataResponse } from '../api/types';
import { ModalShell } from './ModalShell';
import './TableViewerModal.css';

const PAGE_SIZE = 100;
const MAX_CELL_LENGTH = 200;

interface TableViewerModalProps {
  isOpen: boolean;
  projectId: string;
  tableName: string;
  onClose: () => void;
}

function formatCellValue(value: unknown): { display: string; full: string | null; isNull: boolean } {
  if (value === null || value === undefined) {
    return { display: 'NULL', full: null, isNull: true };
  }

  const str = String(value);
  if (str.length > MAX_CELL_LENGTH) {
    return { display: str.substring(0, MAX_CELL_LENGTH) + '...', full: str, isNull: false };
  }

  return { display: str, full: null, isNull: false };
}

export function TableViewerModal({ isOpen, projectId, tableName, onClose }: TableViewerModalProps) {
  const [data, setData] = useState<TableDataResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [sortBy, setSortBy] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDirection>('asc');

  // Reset sort/pagination when the table changes (defensive — if the modal
  // stays open and the user opens a different table)
  useEffect(() => {
    setSortBy(null);
    setSortDir('asc');
    setOffset(0);
  }, [tableName]);

  // Fetch table data when modal opens or pagination/sort changes
  useEffect(() => {
    if (!isOpen) {
      setData(null);
      setError(null);
      setOffset(0);
      setSortBy(null);
      setSortDir('asc');
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchTableData(projectId, tableName, PAGE_SIZE, offset, sortBy, sortDir)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Failed to load table data');
          setLoading(false);
          // If the server rejected the sort column (e.g. table schema
          // changed), clear sort state so the user can recover.
          if (err instanceof ProjectDbApiError && err.errorCode === 'invalid_sort') {
            setSortBy(null);
            setSortDir('asc');
          }
        }
      });

    return () => {
      cancelled = true;
    };
  }, [isOpen, projectId, tableName, offset, sortBy, sortDir]);

  const handlePrevPage = useCallback(() => {
    setOffset((prev) => Math.max(0, prev - PAGE_SIZE));
  }, []);

  const handleNextPage = useCallback(() => {
    setOffset((prev) => prev + PAGE_SIZE);
  }, []);

  // Three-state cycle: none -> asc -> desc -> none
  const handleSort = useCallback((col: string) => {
    setOffset(0);
    if (sortBy !== col) {
      setSortBy(col);
      setSortDir('asc');
      return;
    }
    if (sortDir === 'asc') {
      setSortDir('desc');
    } else {
      setSortBy(null);
      setSortDir('asc');
    }
  }, [sortBy, sortDir]);

  const hasData = data && data.columns.length > 0;
  const showingFrom = hasData ? offset + 1 : 0;
  const showingTo = hasData ? Math.min(offset + data!.rows.length, data!.total_rows) : 0;
  const hasPrevPage = offset > 0;
  const hasNextPage = hasData && (offset + PAGE_SIZE) < data!.total_rows;

  return (
    <ModalShell isOpen={isOpen} onClose={onClose} overlayClassName="table-viewer-overlay" modalClassName="table-viewer-modal">
      <div className="table-viewer-header">
        <div className="table-viewer-title-group">
          <h2 className="table-viewer-title" title={tableName}>{tableName}</h2>
          {hasData && (
            <span className="table-viewer-meta">
              {data!.total_rows.toLocaleString()} row{data!.total_rows !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        <button className="table-viewer-close-button" onClick={onClose}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
      <div className="table-viewer-content">
        {loading && !hasData ? (
          <div className="table-viewer-loading">Loading...</div>
        ) : error ? (
          <div className="table-viewer-error">{error}</div>
        ) : !hasData ? (
          <div className="table-viewer-empty">Table is empty</div>
        ) : (
          <div className={`table-viewer-table-wrapper${loading ? ' is-refetching' : ''}`}>
            <table className="table-viewer-table">
              <thead>
                <tr>
                  {data!.columns.map((col) => {
                    const isActive = sortBy === col;
                    const arrow = !isActive ? '' : sortDir === 'asc' ? ' \u25B2' : ' \u25BC';
                    return (
                      <th
                        key={col}
                        className={`sortable${isActive ? ' sorted' : ''}`}
                        onClick={() => handleSort(col)}
                        title={`Sort by ${col}`}
                      >
                        {col}{arrow}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {data!.rows.map((row, rowIdx) => (
                  <tr key={rowIdx}>
                    {row.map((cell, cellIdx) => {
                      const { display, full, isNull } = formatCellValue(cell);
                      return (
                        <td
                          key={cellIdx}
                          className={isNull ? 'cell-null' : ''}
                          title={full || undefined}
                        >
                          {display}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {hasData && (hasPrevPage || hasNextPage) && (
        <div className="table-viewer-pagination">
          <span className="table-viewer-pagination-info">
            Showing {showingFrom.toLocaleString()}-{showingTo.toLocaleString()} of {data!.total_rows.toLocaleString()}
          </span>
          <div className="table-viewer-pagination-buttons">
            <button
              className="table-viewer-pagination-btn"
              onClick={handlePrevPage}
              disabled={!hasPrevPage}
            >
              Previous
            </button>
            <button
              className="table-viewer-pagination-btn"
              onClick={handleNextPage}
              disabled={!hasNextPage}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </ModalShell>
  );
}
