/**
 * Modal for creating a new folder in the workspace file browser.
 * Mirrors the NewProjectModal pattern: portal-mounted overlay, auto-focus
 * input, trim-on-submit, inline error display, stays open on error.
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { ModalShell } from './ModalShell';
import './NewFolderModal.css';

interface NewFolderModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
}

export function NewFolderModal({ isOpen, onClose, onSubmit }: NewFolderModalProps) {
  const [name, setName] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset state and focus input whenever the modal opens
  useEffect(() => {
    if (isOpen) {
      setName('');
      setError(null);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || isCreating) return;

    setIsCreating(true);
    setError(null);

    try {
      await onSubmit(trimmed);
      // Close on success
      onClose();
    } catch (err) {
      // Display error inside the modal; keep modal open so the user can retry
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Failed to create folder');
      }
    } finally {
      setIsCreating(false);
    }
  }, [name, isCreating, onSubmit, onClose]);

  return (
    <ModalShell isOpen={isOpen} onClose={onClose} overlayClassName="new-folder-overlay" modalClassName="new-folder-modal">
      <div className="new-folder-header">
        <h2>New Folder</h2>
        <button className="new-folder-close-button" onClick={onClose}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
      <form className="new-folder-body" onSubmit={handleSubmit}>
        <label htmlFor="folder-name-input" className="new-folder-label">
          Folder Name
        </label>
        <input
          ref={inputRef}
          id="folder-name-input"
          type="text"
          className="new-folder-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. notes, drafts, data..."
          maxLength={100}
          disabled={isCreating}
        />
        {error && <div className="new-folder-error">{error}</div>}
        <div className="new-folder-actions">
          <button
            type="button"
            className="new-folder-cancel-button"
            onClick={onClose}
            disabled={isCreating}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="new-folder-create-button"
            disabled={!name.trim() || isCreating}
          >
            {isCreating ? 'Creating...' : 'Create Folder'}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
