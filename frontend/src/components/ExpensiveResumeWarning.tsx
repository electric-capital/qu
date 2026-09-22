/**
 * Blocking warning card shown above the composer when the server flags a
 * conversation as expensive to resume (long-idle, long-context, costly
 * model -- see chat/expensive_resume.py). The composer stays disabled until
 * the user picks one of the alternatives or explicitly clicks through
 * "Continue anyway"; a "Learn more" link opens the TokenCostModal.
 */

import { useState, useCallback } from 'react';
import type { ExpensiveResumeInfo } from '../api/types';
import { TokenCostModal } from './TokenCostModal';
import './ExpensiveResumeWarning.css';

interface ExpensiveResumeWarningProps {
  info: ExpensiveResumeInfo;
  /** Compact the conversation's history on the server (summary + recent
   *  messages) and unlock the composer. Async so the card can show a busy
   *  state and surface errors. */
  onCompact: () => Promise<void>;
  /** Duplicate this conversation's workspace into a fresh chat and navigate
   *  there. Async so the card can show a busy state and surface errors. */
  onDuplicateWorkspace: () => Promise<void>;
  /** Open the Create Project from Chat modal (owned by the host). */
  onCreateProject: () => void;
  /** Acknowledge the cost and unlock the composer. */
  onContinueAnyway: () => void;
}

/** "412K tokens" / "1.2M tokens" style compact token count. */
export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  }
  if (tokens >= 1_000) {
    return `${Math.round(tokens / 1_000)}K`;
  }
  return `${tokens}`;
}

/** Coarse human idle duration ("2 hours", "3 days"). */
export function formatIdleDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  if (hours < 1) return 'less than an hour';
  if (hours < 48) return `${hours} hour${hours === 1 ? '' : 's'}`;
  const days = Math.floor(hours / 24);
  return `${days} days`;
}

/** "about $2.20" / "about $12" style rough dollar figure. */
export function formatResumeCost(usd: number): string {
  return usd >= 10 ? `$${Math.round(usd)}` : `$${usd.toFixed(2)}`;
}

export function ExpensiveResumeWarning({
  info,
  onCompact,
  onDuplicateWorkspace,
  onCreateProject,
  onContinueAnyway,
}: ExpensiveResumeWarningProps) {
  const [isDuplicating, setIsDuplicating] = useState(false);
  const [isCompacting, setIsCompacting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isLearnMoreOpen, setIsLearnMoreOpen] = useState(false);
  const isBusy = isDuplicating || isCompacting;

  const handleDuplicate = useCallback(async () => {
    if (isBusy) return;
    setIsDuplicating(true);
    setActionError(null);
    try {
      await onDuplicateWorkspace();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to duplicate workspace');
    } finally {
      setIsDuplicating(false);
    }
  }, [isBusy, onDuplicateWorkspace]);

  const handleCompact = useCallback(async () => {
    if (isBusy) return;
    setIsCompacting(true);
    setActionError(null);
    try {
      await onCompact();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to compact conversation');
    } finally {
      setIsCompacting(false);
    }
  }, [isBusy, onCompact]);

  return (
    <div className="expensive-resume-warning" role="alert">
      <div className="expensive-resume-header">
        <span className="expensive-resume-icon" aria-hidden="true">⚠</span>
        <span className="expensive-resume-title">
          Continuing this chat will be expensive
        </span>
        <button
          type="button"
          className="expensive-resume-learn-more"
          onClick={() => setIsLearnMoreOpen(true)}
        >
          Learn more
        </button>
      </div>
      <p className="expensive-resume-body">
        {info.estimated_resume_cost_usd != null ? (
          <>Your next message here will cost
            about <strong>{formatResumeCost(info.estimated_resume_cost_usd)}</strong> —
            before you even get a reply. </>
        ) : (
          <>Your next message here will be surprisingly expensive. </>
        )}
        This chat has grown very long
        (~{formatTokenCount(info.context_tokens)} tokens of history) and hasn't
        been used for {formatIdleDuration(info.idle_seconds)}, so the whole
        conversation has to be re-read from the beginning at full price — and
        every message after that keeps paying for it. Some cheaper options:
      </p>
      <div className="expensive-resume-actions">
        <button
          type="button"
          className="expensive-resume-action"
          onClick={handleDuplicate}
          disabled={isBusy}
        >
          <span className="expensive-resume-action-label">
            {isDuplicating ? 'Duplicating…' : 'Duplicate Workspace'}
          </span>
          <span className="expensive-resume-action-desc">
            Start a fresh chat that keeps this chat's files — but not its
            long, costly history.
          </span>
        </button>
        <button
          type="button"
          className="expensive-resume-action"
          onClick={onCreateProject}
          disabled={isBusy}
        >
          <span className="expensive-resume-action-label">Create Project from Chat</span>
          <span className="expensive-resume-action-desc">
            Move the files into a project, then ask follow-up questions in
            short, inexpensive chats.
          </span>
        </button>
        <button
          type="button"
          className="expensive-resume-action"
          onClick={handleCompact}
          disabled={isBusy}
        >
          <span className="expensive-resume-action-label">
            {isCompacting ? 'Compacting…' : 'Compact this chat'}
          </span>
          <span className="expensive-resume-action-desc">
            Summarize the long history into a short recap (recent messages
            are kept as-is).
            {info.estimated_compaction_cost_usd != null ? (
              <> A one-time {formatResumeCost(info.estimated_compaction_cost_usd)} summarization
                cost — then every message after it gets much cheaper.</>
            ) : (
              <> A one-time summarization cost — then every message after it
                gets much cheaper.</>
            )}
          </span>
        </button>
        <button
          type="button"
          className="expensive-resume-action expensive-resume-action-continue"
          onClick={onContinueAnyway}
          disabled={isBusy}
        >
          <span className="expensive-resume-action-label">Continue anyway</span>
          <span className="expensive-resume-action-desc">
            I understand the cost — let me keep chatting here.
          </span>
        </button>
      </div>
      {actionError && (
        <div className="expensive-resume-error">{actionError}</div>
      )}

      <TokenCostModal
        isOpen={isLearnMoreOpen}
        onClose={() => setIsLearnMoreOpen(false)}
      />
    </div>
  );
}
