import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchMemories, createMemory, updateMemory, archiveMemory, unarchiveMemory, deleteMemory } from '../../api/client';
import type { Memory } from '../../api/types';
import './MemoriesSection.css';

export function MemoriesSection() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [isLoadingMemories, setIsLoadingMemories] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [newMemoryContent, setNewMemoryContent] = useState('');
  const [memorySaveStatus, setMemorySaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const memorySaveTimeoutRef = useRef<number | null>(null);

  // Load memories on mount and when showArchived changes
  useEffect(() => {
    const loadMemories = async () => {
      setIsLoadingMemories(true);
      try {
        const response = await fetchMemories(showArchived);
        setMemories(response.memories);
      } catch (error) {
        console.error('Failed to load memories:', error);
      } finally {
        setIsLoadingMemories(false);
      }
    };

    loadMemories();
  }, [showArchived]);

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (memorySaveTimeoutRef.current) clearTimeout(memorySaveTimeoutRef.current);
    };
  }, []);

  const handleCreateMemory = useCallback(async () => {
    if (!newMemoryContent.trim()) return;
    setMemorySaveStatus('saving');
    try {
      const memory = await createMemory(newMemoryContent);
      setMemories(prev => [memory, ...prev]);
      setNewMemoryContent('');
      setIsCreating(false);
      setMemorySaveStatus('saved');
      if (memorySaveTimeoutRef.current) clearTimeout(memorySaveTimeoutRef.current);
      memorySaveTimeoutRef.current = window.setTimeout(() => setMemorySaveStatus('idle'), 2000);
    } catch (error) {
      console.error('Failed to create memory:', error);
      setMemorySaveStatus('error');
      if (memorySaveTimeoutRef.current) clearTimeout(memorySaveTimeoutRef.current);
      memorySaveTimeoutRef.current = window.setTimeout(() => setMemorySaveStatus('idle'), 3000);
    }
  }, [newMemoryContent]);

  const handleUpdateMemory = useCallback(async (memoryId: string) => {
    if (!editingContent.trim()) return;
    setMemorySaveStatus('saving');
    try {
      const updated = await updateMemory(memoryId, editingContent);
      setMemories(prev => prev.map(m => m.id === memoryId ? updated : m));
      setEditingMemoryId(null);
      setEditingContent('');
      setMemorySaveStatus('saved');
      if (memorySaveTimeoutRef.current) clearTimeout(memorySaveTimeoutRef.current);
      memorySaveTimeoutRef.current = window.setTimeout(() => setMemorySaveStatus('idle'), 2000);
    } catch (error) {
      console.error('Failed to update memory:', error);
      setMemorySaveStatus('error');
      if (memorySaveTimeoutRef.current) clearTimeout(memorySaveTimeoutRef.current);
      memorySaveTimeoutRef.current = window.setTimeout(() => setMemorySaveStatus('idle'), 3000);
    }
  }, [editingContent]);

  const handleArchiveMemory = useCallback(async (memoryId: string) => {
    try {
      await archiveMemory(memoryId);
      if (showArchived) {
        setMemories(prev => prev.map(m => m.id === memoryId ? { ...m, archived: true } : m));
      } else {
        setMemories(prev => prev.filter(m => m.id !== memoryId));
      }
    } catch (error) {
      console.error('Failed to archive memory:', error);
    }
  }, [showArchived]);

  const handleUnarchiveMemory = useCallback(async (memoryId: string) => {
    try {
      const updated = await unarchiveMemory(memoryId);
      setMemories(prev => prev.map(m => m.id === memoryId ? updated : m));
    } catch (error) {
      console.error('Failed to unarchive memory:', error);
    }
  }, []);

  const handleDeleteMemory = useCallback(async (memoryId: string) => {
    if (!confirm('Are you sure you want to permanently delete this memory? This cannot be undone.')) {
      return;
    }
    try {
      await deleteMemory(memoryId);
      setMemories(prev => prev.filter(m => m.id !== memoryId));
    } catch (error) {
      console.error('Failed to delete memory:', error);
    }
  }, []);

  const startEditing = useCallback((memory: Memory) => {
    setEditingMemoryId(memory.id);
    setEditingContent(memory.content);
  }, []);

  const cancelEditing = useCallback(() => {
    setEditingMemoryId(null);
    setEditingContent('');
  }, []);

  const startCreating = useCallback(() => {
    setIsCreating(true);
    setNewMemoryContent('');
  }, []);

  const cancelCreating = useCallback(() => {
    setIsCreating(false);
    setNewMemoryContent('');
  }, []);

  return (
    <div className="settings-section">
      <h3>Memories</h3>
      <p className="settings-description">
        Memories are notes and context that the AI assistant uses to personalize
        your experience. You can create, edit, and archive memories here.
      </p>

      <div className="memories-toolbar">
        <button
          className="memories-create-btn"
          onClick={startCreating}
          disabled={isCreating}
        >
          + New Memory
        </button>
        <label className="memories-archived-toggle">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          Show archived
        </label>
      </div>

      {isCreating && (
        <div className="memory-card memory-card-editing">
          <textarea
            className="memory-edit-textarea"
            value={newMemoryContent}
            onChange={(e) => setNewMemoryContent(e.target.value)}
            placeholder="Enter memory content (markdown supported, max 4KB)..."
            rows={4}
            autoFocus
          />
          <div className="memory-card-actions">
            <button
              className="memory-btn memory-btn-save"
              onClick={handleCreateMemory}
              disabled={!newMemoryContent.trim() || memorySaveStatus === 'saving'}
            >
              {memorySaveStatus === 'saving' ? 'Saving...' : 'Save'}
            </button>
            <button
              className="memory-btn memory-btn-cancel"
              onClick={cancelCreating}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {isLoadingMemories ? (
        <div className="settings-loading">Loading memories...</div>
      ) : memories.length === 0 ? (
        <div className="memories-empty">
          {showArchived
            ? 'No memories found.'
            : 'No memories yet. Click "+ New Memory" to create one.'}
        </div>
      ) : (
        <div className="memories-list">
          {memories.map((memory) => (
            <div
              key={memory.id}
              className={`memory-card ${memory.archived ? 'memory-card-archived' : ''}`}
            >
              {editingMemoryId === memory.id ? (
                <>
                  <textarea
                    className="memory-edit-textarea"
                    value={editingContent}
                    onChange={(e) => setEditingContent(e.target.value)}
                    rows={4}
                    autoFocus
                  />
                  <div className="memory-card-actions">
                    <button
                      className="memory-btn memory-btn-save"
                      onClick={() => handleUpdateMemory(memory.id)}
                      disabled={!editingContent.trim() || memorySaveStatus === 'saving'}
                    >
                      {memorySaveStatus === 'saving' ? 'Saving...' : 'Save'}
                    </button>
                    <button
                      className="memory-btn memory-btn-cancel"
                      onClick={cancelEditing}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="memory-card-content">
                    {memory.content}
                  </div>
                  <div className="memory-card-meta">
                    <span className="memory-card-date">
                      {new Date(memory.created_at).toLocaleDateString(undefined, {
                        month: 'short', day: 'numeric', year: 'numeric'
                      })}
                      {memory.updated_at && (
                        <> (edited {new Date(memory.updated_at).toLocaleDateString(undefined, {
                          month: 'short', day: 'numeric', year: 'numeric'
                        })})</>
                      )}
                    </span>
                    {memory.archived && (
                      <span className="memory-badge-archived">Archived</span>
                    )}
                  </div>
                  <div className="memory-card-actions">
                    {!memory.archived && (
                      <button
                        className="memory-btn memory-btn-edit"
                        onClick={() => startEditing(memory)}
                      >
                        Edit
                      </button>
                    )}
                    {memory.archived ? (
                      <button
                        className="memory-btn memory-btn-unarchive"
                        onClick={() => handleUnarchiveMemory(memory.id)}
                      >
                        Unarchive
                      </button>
                    ) : (
                      <button
                        className="memory-btn memory-btn-archive"
                        onClick={() => handleArchiveMemory(memory.id)}
                      >
                        Archive
                      </button>
                    )}
                    <button
                      className="memory-btn memory-btn-delete"
                      onClick={() => handleDeleteMemory(memory.id)}
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {memorySaveStatus === 'saved' && (
        <span className="settings-save-status saved">Memory saved</span>
      )}
      {memorySaveStatus === 'error' && (
        <span className="settings-save-status error">Failed to save</span>
      )}
    </div>
  );
}
