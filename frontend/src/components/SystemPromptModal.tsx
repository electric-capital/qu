/**
 * Modal for viewing the full system prompt used in the current conversation.
 */

import { useState, useEffect } from 'react';
import { fetchSystemPrompt } from '../api/client';
import { ModalShell } from './ModalShell';
import './SystemPromptModal.css';

interface SystemPromptModalProps {
  isOpen: boolean;
  conversationId: string;
  onClose: () => void;
}

export function SystemPromptModal({ isOpen, conversationId, onClose }: SystemPromptModalProps) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch system prompt when modal opens
  useEffect(() => {
    if (!isOpen) {
      setContent(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchSystemPrompt(conversationId)
      .then((data) => {
        if (!cancelled) {
          setContent(data.system_prompt);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Failed to load system prompt');
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [isOpen, conversationId]);

  return (
    <ModalShell isOpen={isOpen} onClose={onClose} overlayClassName="system-prompt-overlay" modalClassName="system-prompt-modal">
      <div className="system-prompt-header">
        <h2 className="system-prompt-title">System Prompt</h2>
        <button className="system-prompt-close-button" onClick={onClose}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
      <div className="system-prompt-content">
        {loading ? (
          <div className="system-prompt-loading">Loading...</div>
        ) : error ? (
          <div className="system-prompt-error">{error}</div>
        ) : content == null ? (
          <div className="system-prompt-empty">No system prompt available yet. Send a message to generate one.</div>
        ) : (
          <pre className="system-prompt-pre">{content}</pre>
        )}
      </div>
    </ModalShell>
  );
}
