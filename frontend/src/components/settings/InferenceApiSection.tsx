import { useState, useEffect, useCallback } from 'react';
import { Check, Copy } from 'lucide-react';
import {
  fetchInferenceApiKeys,
  createInferenceApiKey,
  deleteInferenceApiKey,
} from '../../api/client';
import type { InferenceApiKey, CreatedInferenceApiKey } from '../../api/types';
import './InferenceApiSection.css';

const MAX_KEY_NAME_LENGTH = 100;

function formatDate(iso: string | null): string {
  if (!iso) return 'Never';
  const date = new Date(iso);
  if (isNaN(date.getTime())) return 'Never';
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function InferenceApiSection() {
  const [keys, setKeys] = useState<InferenceApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [actionError, setActionError] = useState('');
  // The freshly created key, shown exactly once (the token is never
  // retrievable again).
  const [createdKey, setCreatedKey] = useState<CreatedInferenceApiKey | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const response = await fetchInferenceApiKeys();
        setKeys(response.keys);
      } catch (error) {
        console.error('Failed to load inference API keys:', error);
        setLoadError('Failed to load API keys.');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const handleCreate = useCallback(async () => {
    const name = newName.trim();
    if (!name || creating) return;
    setCreating(true);
    setActionError('');
    try {
      const created = await createInferenceApiKey(name);
      setCreatedKey(created);
      setCopied(false);
      setNewName('');
      setKeys((prev) => [
        {
          id: created.id,
          user_id: created.user_id,
          name: created.name,
          token_hint: created.token_hint,
          created_at: created.created_at,
          last_used_at: created.last_used_at,
        },
        ...prev,
      ]);
    } catch (error) {
      console.error('Failed to create inference API key:', error);
      setActionError(error instanceof Error ? error.message : 'Failed to create key');
    } finally {
      setCreating(false);
    }
  }, [newName, creating]);

  const handleDelete = useCallback(async (key: InferenceApiKey) => {
    if (!confirm(`Delete the API key "${key.name}"? Applications using it will stop working immediately.`)) {
      return;
    }
    setActionError('');
    try {
      await deleteInferenceApiKey(key.id);
      setKeys((prev) => prev.filter((k) => k.id !== key.id));
      setCreatedKey((prev) => (prev && prev.id === key.id ? null : prev));
    } catch (error) {
      console.error('Failed to delete inference API key:', error);
      setActionError(error instanceof Error ? error.message : 'Failed to delete key');
    }
  }, []);

  const handleCopy = useCallback(async () => {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy token:', error);
    }
  }, [createdKey]);

  return (
    <div className="settings-section">
      <h3>Inference API</h3>
      <p className="settings-description">
        API keys for Quest's one-shot inference endpoint. Applications send a single
        prompt to <code>POST /api/inference</code> with a key as a bearer token; the
        prompt runs with your data access and the final markdown response is returned.
        Runs appear in your sidebar as read-only conversations.
      </p>

      {loading ? (
        <div className="settings-loading">Loading...</div>
      ) : loadError ? (
        <p className="inf-key-error">{loadError}</p>
      ) : (
        <>
          {createdKey && (
            <div className="inf-key-created">
              <div className="inf-key-created-title">
                Key "{createdKey.name}" created
              </div>
              <p className="inf-key-created-warning">
                Copy the key now — it will not be shown again.
              </p>
              <div className="inf-key-token-row">
                <code className="inf-key-token">{createdKey.token}</code>
                <button
                  className={`inf-key-copy-btn${copied ? ' copied' : ''}`}
                  onClick={handleCopy}
                  title={copied ? 'Copied!' : 'Copy to clipboard'}
                >
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <button
                className="inf-key-dismiss-btn"
                onClick={() => setCreatedKey(null)}
              >
                Done
              </button>
            </div>
          )}

          <div className="inf-key-add-row">
            <input
              type="text"
              className="inf-key-input"
              placeholder="Key name (e.g. dashboard-service)"
              value={newName}
              maxLength={MAX_KEY_NAME_LENGTH}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleCreate();
                }
              }}
            />
            <button
              className="inf-key-add-button"
              onClick={handleCreate}
              disabled={!newName.trim() || creating}
            >
              {creating ? 'Generating...' : 'Generate Key'}
            </button>
          </div>
          {actionError && <p className="inf-key-error">{actionError}</p>}

          {keys.length === 0 ? (
            <p className="inf-key-empty">
              No API keys yet. Generate one to call the inference API.
            </p>
          ) : (
            <ul className="inf-key-list">
              {keys.map((key) => (
                <li key={key.id} className="inf-key-row">
                  <div className="inf-key-row-main">
                    <span className="inf-key-name">{key.name}</span>
                    <span className="inf-key-hint">...{key.token_hint}</span>
                  </div>
                  <div className="inf-key-row-meta">
                    <span>Created {formatDate(key.created_at)}</span>
                    <span>Last used: {formatDate(key.last_used_at)}</span>
                  </div>
                  <button
                    className="inf-key-delete"
                    onClick={() => handleDelete(key)}
                    aria-label={`Delete key ${key.name}`}
                    title="Delete key"
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
