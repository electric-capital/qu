/**
 * "Learn more" modal explaining token costs on long conversations: why
 * resuming an old, long conversation is expensive (prompt-cache expiry)
 * and why a project with short focused conversations is a cheaper pattern
 * for repeated asks against the same data. Opened from the
 * ExpensiveResumeWarning card.
 */

import { ModalShell } from './ModalShell';
import './TokenCostModal.css';

interface TokenCostModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function TokenCostModal({ isOpen, onClose }: TokenCostModalProps) {
  return (
    <ModalShell isOpen={isOpen} onClose={onClose} overlayClassName="token-cost-overlay" modalClassName="token-cost-modal">
      <div className="token-cost-header">
        <h2>Why long conversations get expensive</h2>
        <button className="token-cost-close-button" onClick={onClose} aria-label="Close">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
      <div className="token-cost-content">
        <h3>Every message re-sends the whole conversation</h3>
        <p>
          Language models are stateless: each new message sends the entire
          conversation history — every prior message, tool call, and file
          the model has read — back to the model as input tokens. The
          longer a conversation grows, the more tokens every single message
          costs.
        </p>

        <h3>Caching makes active conversations cheap…</h3>
        <p>
          While you're actively chatting, the provider caches the
          conversation's context. Follow-up messages only pay full price
          for the <em>new</em> part; the cached history is billed at a
          small fraction of the normal input rate.
        </p>

        <h3>…but the cache expires when you step away</h3>
        <p>
          The cache only lives for a short window of inactivity. Come back
          hours later and it's gone: your next message re-processes the
          <em> entire</em> history at the full input rate, and re-writes
          the cache on top. On a large conversation with a premium model
          (like Opus), a single resumed message can cost several dollars
          before the model writes a word — and every message after that
          keeps carrying the full history.
        </p>

        <h3>A cheaper pattern: projects + short conversations</h3>
        <p>
          If you need to make many requests against the same set of data,
          don't grow one giant conversation. Instead:
        </p>
        <ol>
          <li>Create a <strong>project</strong> and collect the data into
            its shared workspace (one conversation can do the gathering).</li>
          <li>Start a <strong>new, short conversation</strong> in the
            project for each specific ask.</li>
        </ol>
        <p>
          Every conversation in a project shares the workspace files, but
          each one only reads the files it actually needs — instead of
          dragging the full research history behind every message. Many
          short conversations against shared data are far cheaper (and
          usually give better answers) than one long conversation that
          does everything.
        </p>
      </div>
    </ModalShell>
  );
}
