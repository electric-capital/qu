/**
 * ConversationHeader -- the title unit at the top of an open chat.
 *
 * Renders the conversation title followed by a chevron; clicking either
 * opens a small dropdown (Rename / Archive-or-Unarchive) styled after the
 * Claude.ai chat header. Rename swaps the title for an inline input.
 *
 * Title/archived state lives in useConversation (hydrated from
 * GET /conversations/{id}); the header updates it optimistically through
 * the `onRenamed` / `onArchivedChange` callbacks. The sidebar keeps itself
 * in sync through the server-published `conversation_list_changed` event
 * both endpoints emit. When the model names the chat itself (the
 * `set_conversation_name` tool), useConversation picks up the resulting
 * `conversation_updated` event so this title follows the sidebar row.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Archive, ArchiveRestore, ChevronDown, Pencil } from 'lucide-react';
import {
  archiveConversation,
  renameConversation,
  unarchiveConversation,
} from '../api/client';
import './ConversationHeader.css';

interface ConversationHeaderProps {
  conversationId: string;
  /** Display title: custom name when set, else the auto title. */
  title: string;
  customName: string | null;
  archived: boolean;
  onRenamed: (customName: string | null) => void;
  onArchivedChange: (archived: boolean) => void;
}

export function ConversationHeader({
  conversationId,
  title,
  customName,
  archived,
  onRenamed,
  onArchivedChange,
}: ConversationHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);
  // Guards the blur-after-Enter double submit on the rename input.
  const renameSubmittedRef = useRef(false);

  // Reset transient UI when switching conversations.
  useEffect(() => {
    setMenuOpen(false);
    setRenaming(false);
    setRenameValue('');
  }, [conversationId]);

  // Close the menu on any outside click (the toggle stops propagation).
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [menuOpen]);

  const startRename = useCallback(() => {
    setMenuOpen(false);
    renameSubmittedRef.current = false;
    // Placeholder titles start empty so the user doesn't have to clear them.
    setRenameValue(customName ?? (title === 'New Chat' ? '' : title));
    setRenaming(true);
  }, [customName, title]);

  useEffect(() => {
    if (renaming) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [renaming]);

  const submitRename = useCallback(() => {
    if (renameSubmittedRef.current) return;
    renameSubmittedRef.current = true;
    setRenaming(false);
    const trimmed = renameValue.trim();
    const next = trimmed || null; // empty = revert to the auto title
    if (next === customName) return;
    const previous = customName;
    onRenamed(next);
    renameConversation(conversationId, next).catch((err) => {
      console.error('Failed to rename conversation:', err);
      onRenamed(previous);
    });
  }, [conversationId, customName, onRenamed, renameValue]);

  const cancelRename = useCallback(() => {
    renameSubmittedRef.current = true;
    setRenaming(false);
    setRenameValue('');
  }, []);

  const toggleArchived = useCallback(() => {
    setMenuOpen(false);
    const next = !archived;
    onArchivedChange(next);
    const call = next ? archiveConversation : unarchiveConversation;
    call(conversationId).catch((err) => {
      console.error('Failed to update archive state:', err);
      onArchivedChange(!next);
    });
  }, [archived, conversationId, onArchivedChange]);

  // Single-key shortcuts while the menu is open (mirrors the hint letters).
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === 'r') { e.preventDefault(); startRename(); }
      else if (key === 'a') { e.preventDefault(); toggleArchived(); }
      else if (key === 'escape') { setMenuOpen(false); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [menuOpen, startRename, toggleArchived]);

  return (
    <div className="conversation-header">
      <div className="conversation-header-unit">
        {renaming ? (
          <input
            ref={renameInputRef}
            className="conversation-header-rename-input"
            value={renameValue}
            placeholder="Chat name"
            maxLength={100}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); submitRename(); }
              else if (e.key === 'Escape') { e.preventDefault(); cancelRename(); }
            }}
            onBlur={submitRename}
            aria-label="Rename conversation"
          />
        ) : (
          <button
            type="button"
            className={`conversation-header-title-button${menuOpen ? ' open' : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen((open) => !open);
            }}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title={title}
          >
            <span className="conversation-header-title">{title}</span>
            {archived && <span className="conversation-header-badge">Archived</span>}
            <span className="conversation-header-chevron" aria-hidden="true">
              <ChevronDown size={16} />
            </span>
          </button>
        )}

        {menuOpen && (
          <div
            className="conversation-header-menu"
            role="menu"
            onClick={(e) => e.stopPropagation()}
          >
            <button type="button" className="conversation-header-menu-item" role="menuitem" onClick={startRename}>
              <Pencil size={16} className="conversation-header-menu-icon" />
              <span>Rename</span>
              <span className="conversation-header-menu-key">R</span>
            </button>
            <button type="button" className="conversation-header-menu-item" role="menuitem" onClick={toggleArchived}>
              {archived
                ? <ArchiveRestore size={16} className="conversation-header-menu-icon" />
                : <Archive size={16} className="conversation-header-menu-icon" />}
              <span>{archived ? 'Unarchive' : 'Archive'}</span>
              <span className="conversation-header-menu-key">A</span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
