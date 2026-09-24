/**
 * Context usage indicator component.
 *
 * Shows a compact percentage badge below the model selector row indicating
 * how much of the model's context window has been consumed. A hover tooltip
 * shows the detailed breakdown (e.g., "70K / 200K max").
 */

import React from 'react';
import { getModelInfo } from '../constants/models';
import './ContextIndicator.css';

interface ContextIndicatorProps {
  contextTokens: number | null;
  maxContextTokens: number | null;
  modelId: string;
  onInfoClick?: () => void;
}

/**
 * Format a token count into a human-readable string.
 * - Under 1000: show raw number (e.g., "950")
 * - 1000-999999: show with K suffix (e.g., "70K", "1.5K")
 * - 1000000+: show with M suffix (e.g., "1M", "1.5M")
 */
function formatTokenCount(count: number): string {
  if (count < 1000) {
    return String(count);
  }
  if (count < 1_000_000) {
    const k = count / 1000;
    // Use integer if it's a round number, otherwise one decimal
    return k === Math.floor(k) ? `${k}K` : `${k.toFixed(1).replace(/\.0$/, '')}K`;
  }
  const m = count / 1_000_000;
  return m === Math.floor(m) ? `${m}M` : `${m.toFixed(1).replace(/\.0$/, '')}M`;
}

export const ContextIndicator = React.memo(function ContextIndicator({
  contextTokens,
  maxContextTokens,
  modelId,
  onInfoClick,
}: ContextIndicatorProps) {
  // Don't render if no context data yet
  if (contextTokens == null) {
    return null;
  }

  // Determine max context: prefer stats-reported value, fall back to model constant
  let effectiveMax = maxContextTokens;
  if (effectiveMax == null || effectiveMax === 0) {
    effectiveMax = getModelInfo(modelId)?.maxInputTokens ?? null;
  }

  // If we still don't have a max, don't render
  if (effectiveMax == null || effectiveMax === 0) {
    return null;
  }

  const percentage = Math.round((contextTokens / effectiveMax) * 100);
  // Clamp to 100% in case of slight overcount
  const displayPercentage = Math.min(percentage, 100);

  // Color based on usage level
  let colorClass = 'context-normal';
  if (percentage >= 90) {
    colorClass = 'context-danger';
  } else if (percentage >= 70) {
    colorClass = 'context-warning';
  }

  const tooltipText = `${formatTokenCount(contextTokens)} / ${formatTokenCount(effectiveMax)} max`;

  return (
    <div className="context-indicator-container">
      <span className={`context-indicator-badge ${colorClass}`}>
        {displayPercentage}% context
      </span>
      {onInfoClick && (
        <button className="context-indicator-info-button" onClick={onInfoClick} title="View system prompt">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
        </button>
      )}
      <span className="context-indicator-tooltip">{tooltipText}</span>
    </div>
  );
});
