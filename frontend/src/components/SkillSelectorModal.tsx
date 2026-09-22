/**
 * Skill selector modal for loading skills into a conversation.
 * Uses createPortal for rendering outside the component tree.
 * Modeled after SearchModal.tsx.
 */

import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { fetchSkills, fetchAutoloadedSkillIds } from '../api/client';
import type { Skill } from '../api/types';
import { ModalShell } from './ModalShell';
import './SkillSelectorModal.css';

interface SkillSelectorModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (selectedSkillIds: string[]) => void;
  alreadyLoadedSkillIds: string[];
  autoloadedSkillIds: string[];
}

export function SkillSelectorModal({
  isOpen,
  onClose,
  onConfirm,
  alreadyLoadedSkillIds,
  autoloadedSkillIds,
}: SkillSelectorModalProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [allSkills, setAllSkills] = useState<Skill[]>([]);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolvedAutoloadIds, setResolvedAutoloadIds] = useState<string[]>(autoloadedSkillIds);

  const inputRef = useRef<HTMLInputElement>(null);

  // Sets for O(1) lookup
  const loadedSet = useMemo(() => new Set(alreadyLoadedSkillIds), [alreadyLoadedSkillIds]);
  const autoloadSet = useMemo(() => new Set(resolvedAutoloadIds), [resolvedAutoloadIds]);

  // Fetch all skills and autoloaded IDs when modal opens
  useEffect(() => {
    if (!isOpen) return;

    // Reset state on open
    setSearchQuery('');
    setCheckedIds(new Set());
    setError(null);

    const loadData = async () => {
      setLoading(true);
      try {
        const [skillsResp, autoloadResp] = await Promise.all([
          fetchSkills(),
          fetchAutoloadedSkillIds(),
        ]);
        setAllSkills(skillsResp.skills);
        setResolvedAutoloadIds(autoloadResp.skill_ids);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load skills');
      } finally {
        setLoading(false);
      }
    };

    loadData();
    setTimeout(() => inputRef.current?.focus(), 50);
  }, [isOpen]);

  // Client-side filtering by name/description
  const filteredSkills = useMemo(() => {
    if (!searchQuery.trim()) return allSkills;
    const q = searchQuery.toLowerCase();
    return allSkills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q)
    );
  }, [allSkills, searchQuery]);

  const handleToggle = useCallback((skillId: string) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (next.has(skillId)) {
        next.delete(skillId);
      } else {
        next.add(skillId);
      }
      return next;
    });
  }, []);

  const handleConfirm = useCallback(() => {
    onConfirm(Array.from(checkedIds));
    onClose();
  }, [checkedIds, onConfirm, onClose]);

  return (
    <ModalShell
      isOpen={isOpen}
      onClose={onClose}
      overlayClassName="skill-selector-overlay"
      modalClassName="skill-selector-modal"
    >
      {/* Header */}
      <div className="skill-selector-header">
        <h2>Load Skills</h2>
        <button className="skill-selector-close-button" onClick={onClose}>
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>

      {/* Search input */}
      <div className="skill-selector-input-wrapper">
        <svg
          className="skill-selector-input-icon"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        <input
          ref={inputRef}
          type="text"
          className="skill-selector-input"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Filter skills..."
        />
      </div>

      {/* Skills list */}
      <div className="skill-selector-list">
        {loading && (
          <div className="skill-selector-status">Loading skills...</div>
        )}

        {error && <div className="skill-selector-error">{error}</div>}

        {!loading && !error && filteredSkills.length === 0 && (
          <div className="skill-selector-status">
            {allSkills.length === 0
              ? 'No skills available'
              : 'No skills match your search'}
          </div>
        )}

        {!loading &&
          filteredSkills.map((skill) => {
            const isAutoloaded = autoloadSet.has(skill.id);
            const isLoaded = loadedSet.has(skill.id);
            const isDisabled = isAutoloaded || isLoaded;
            const isChecked = checkedIds.has(skill.id);

            return (
              <label
                key={skill.id}
                className={`skill-selector-row${isDisabled ? ' disabled' : ''}`}
              >
                <input
                  type="checkbox"
                  className="skill-selector-checkbox"
                  checked={isChecked || isDisabled}
                  disabled={isDisabled}
                  onChange={() => handleToggle(skill.id)}
                />
                <div className="skill-selector-row-content">
                  <div className="skill-selector-row-header">
                    <span className="skill-selector-name">{skill.name}</span>
                    {isAutoloaded && (
                      <span className="skill-selector-badge auto-loaded">
                        auto-loaded
                      </span>
                    )}
                    {isLoaded && !isAutoloaded && (
                      <span className="skill-selector-badge loaded">
                        loaded
                      </span>
                    )}
                    {skill.visibility !== 'private' && (
                      <span className="skill-selector-badge visibility">
                        {skill.visibility}
                      </span>
                    )}
                  </div>
                  {skill.description && (
                    <div className="skill-selector-description">
                      {skill.description}
                    </div>
                  )}
                </div>
              </label>
            );
          })}
      </div>

      {/* Footer with confirm/cancel */}
      <div className="skill-selector-footer">
        <span className="skill-selector-footer-hint">
          <kbd>Esc</kbd> to close
        </span>
        <div className="skill-selector-footer-buttons">
          <button className="skill-selector-cancel-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="skill-selector-confirm-btn"
            onClick={handleConfirm}
            disabled={checkedIds.size === 0}
          >
            Load {checkedIds.size > 0 ? `(${checkedIds.size})` : ''}
          </button>
        </div>
      </div>
    </ModalShell>
  );
}
