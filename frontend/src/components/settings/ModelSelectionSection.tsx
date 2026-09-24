import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { fetchModelSelection, updateModelSelection } from '../../api/client';
import type { ModelSelectionListResponse, ModelSelectionRow } from '../../api/types';
import { useConversationContext } from '../../contexts/ConversationContext';
import { SaveActions } from './ServiceCredentialsSection';
import type { SaveStatus } from './ServiceCredentialsSection';
import './ServiceCredentialsSection.css';
import './ModelSelectionSection.css';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/** The editable part of a row, keyed by model id in the draft. */
interface Draft {
  slot: number | null;
  descriptor: string;
  allowPrivate: boolean;
  allowPublic: boolean;
}

function draftOf(row: ModelSelectionRow): Draft {
  return {
    slot: row.slot,
    descriptor: row.descriptor,
    allowPrivate: row.allow_private,
    allowPublic: row.allow_public,
  };
}

function sameDraft(a: Draft, b: Draft): boolean {
  return a.slot === b.slot
    && a.descriptor === b.descriptor
    && a.allowPrivate === b.allowPrivate
    && a.allowPublic === b.allowPublic;
}

const UNAVAILABLE_LABELS: Record<NonNullable<ModelSelectionRow['unavailable_reason']>, string> = {
  not_configured: 'Not configured',
  failing: 'Failing health check',
};

/**
 * Static rendering of the composer's model menu as the draft would show it:
 * the slotted models in slot order (descriptor over model name, or the bare
 * name when the descriptor is empty) above the "All models" row. Reuses the
 * real menu's class names from ChatPanel.css so the preview is faithful;
 * the wrapper undoes the popover positioning.
 */
function ModelMenuPreview({
  rows,
  drafts,
}: {
  rows: ModelSelectionRow[];
  drafts: Record<string, Draft>;
}) {
  const picks = rows
    .filter((row) => drafts[row.id]?.slot !== null && drafts[row.id]?.slot !== undefined)
    .sort((a, b) => (drafts[a.id].slot as number) - (drafts[b.id].slot as number));
  return (
    <div className="model-selection-preview" aria-label="Model menu preview">
      <div className="model-menu" role="presentation">
        {picks.map((row) => {
          const draft = drafts[row.id];
          return (
            <div
              key={row.id}
              className={`model-menu-item${row.available ? '' : ' model-selection-preview-unavailable'}`}
              title={row.available ? undefined : `Hidden from users right now: ${UNAVAILABLE_LABELS[row.unavailable_reason ?? 'not_configured']}`}
            >
              <span className="model-menu-item-text">
                <span className="model-menu-item-label">{draft.descriptor || row.display_name}</span>
                {draft.descriptor && <span className="model-menu-item-sub">{row.display_name}</span>}
              </span>
            </div>
          );
        })}
        {picks.length > 0 && <div className="model-menu-divider" />}
        <div className="model-menu-item">
          <span className="model-menu-item-text">
            <span className="model-menu-item-label">All models</span>
          </span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 18 15 12 9 6"></polyline>
          </svg>
        </div>
      </div>
      {picks.length === 0 && (
        <p className="model-selection-preview-note">
          No model has a slot — the menu opens straight onto "All models".
        </p>
      )}
    </div>
  );
}

/**
 * Admin-only table of every enabled model (one row each) with the per-model
 * selection settings persisted server-side in data/model_selection.json:
 * which top-level slot of the composer's model menu it occupies (if any),
 * the descriptor shown there, and whether it may be used in private and/or
 * public-project conversations. Edits accumulate in a draft and are saved
 * as one full replacement; the preview on the right renders the draft.
 */
export function ModelSelectionSection() {
  const { refreshModelCatalog } = useConversationContext();
  const [data, setData] = useState<ModelSelectionListResponse | null>(null);
  const [loadError, setLoadError] = useState('');
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [saveError, setSaveError] = useState('');
  const statusTimeoutRef = useRef<number | null>(null);

  const applyResponse = useCallback((response: ModelSelectionListResponse) => {
    setData(response);
    setDrafts(Object.fromEntries(response.models.map((row) => [row.id, draftOf(row)])));
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        applyResponse(await fetchModelSelection());
      } catch (error) {
        console.error('Failed to load model selection:', error);
        setLoadError(errorMessage(error, 'Failed to load'));
      }
    };
    load();
    return () => {
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
    };
  }, [applyResponse]);

  const dirty = useMemo(
    () => data !== null && data.models.some((row) => !sameDraft(drafts[row.id], draftOf(row))),
    [data, drafts],
  );

  const updateDraft = useCallback((modelId: string, patch: Partial<Draft>) => {
    setDrafts((prev) => ({ ...prev, [modelId]: { ...prev[modelId], ...patch } }));
  }, []);

  // Slots are unique: giving a model a slot another model holds swaps the
  // two (the other model takes this one's previous slot, possibly none), so
  // nothing silently drops out of the top level.
  const setSlot = useCallback((modelId: string, slot: number | null) => {
    setDrafts((prev) => {
      const previous = prev[modelId].slot;
      const next = { ...prev, [modelId]: { ...prev[modelId], slot } };
      if (slot !== null) {
        const holder = Object.keys(prev).find((id) => id !== modelId && prev[id].slot === slot);
        if (holder) next[holder] = { ...prev[holder], slot: previous };
      }
      return next;
    });
  }, []);

  const handleDiscard = useCallback(() => {
    if (data) applyResponse(data);
    setSaveError('');
    setSaveStatus('idle');
  }, [data, applyResponse]);

  const handleSave = useCallback(async () => {
    if (!data) return;
    setSaveStatus('saving');
    setSaveError('');
    try {
      const response = await updateModelSelection({
        models: data.models.map((row) => {
          const draft = drafts[row.id];
          return {
            id: row.id,
            slot: draft.slot,
            descriptor: draft.descriptor.trim(),
            allow_private: draft.allowPrivate,
            allow_public: draft.allowPublic,
          };
        }),
      });
      applyResponse(response);
      setSaveStatus('saved');
      // The composer menu in this tab reads the module-level catalog fed by
      // GET /app/api/config; re-fetch so the change shows without a reload.
      void refreshModelCatalog();
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = window.setTimeout(() => setSaveStatus('idle'), 2000);
    } catch (error) {
      console.error('Failed to save model selection:', error);
      setSaveStatus('error');
      setSaveError(errorMessage(error, 'Failed to save'));
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = window.setTimeout(() => setSaveStatus('idle'), 4000);
    }
  }, [data, drafts, applyResponse, refreshModelCatalog]);

  // Rows grouped by provider label, preserving server order (Vertex
  // families first, then each instance's models).
  const groups = useMemo(() => {
    if (!data) return [];
    const out: { label: string; rows: ModelSelectionRow[] }[] = [];
    for (const row of data.models) {
      const last = out[out.length - 1];
      if (last && last.label === row.provider_label) last.rows.push(row);
      else out.push({ label: row.provider_label, rows: [row] });
    }
    return out;
  }, [data]);

  const slotOptions = data ? Array.from({ length: data.max_slots }, (_, i) => i + 1) : [];

  return (
    <div className="settings-section">
      <h3>Model Selection</h3>
      <p className="settings-description">
        How the enabled models are offered to users. Pin up to {data?.max_slots ?? 5} models
        to the top level of the composer's model menu with a descriptor of your choice
        (everything else stays under "All models"), and choose whether each model may be
        used in private conversations or in public-project conversations. Applies to all
        users. Admin only.
      </p>
      {loadError ? (
        <div className="svc-cred-load-error">{loadError}</div>
      ) : data === null ? (
        <div className="settings-loading">Loading...</div>
      ) : (
        <div className="model-selection-layout">
          <div className="model-selection-main">
            <table className="model-selection-table">
              <thead>
                <tr>
                  <th className="model-selection-col-model">Model</th>
                  <th className="model-selection-col-slot" title="Position in the top level of the model menu">Top-level slot</th>
                  <th className="model-selection-col-descriptor" title="Label shown for the model in the top level of the menu">Descriptor</th>
                  <th className="model-selection-col-check" title="May be used in standalone and project conversations, routines, Slack and API runs">Private</th>
                  <th className="model-selection-col-check" title="May be used in public-project conversations (internet-enabled sandbox)">Public</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <GroupRows
                    key={group.label}
                    label={group.label}
                    rows={group.rows}
                    drafts={drafts}
                    slotOptions={slotOptions}
                    maxDescriptorLength={data.max_descriptor_length}
                    busy={saveStatus === 'saving'}
                    onSlot={setSlot}
                    onChange={updateDraft}
                  />
                ))}
              </tbody>
            </table>
            <p className="model-selection-note">
              A dot after a model's name means it is currently hidden from users — its
              provider is not configured (grey) or failing its health check (red); fix that
              in Inference Providers. Unticking Private or Public also stops existing
              conversations of that kind from continuing on the model until they switch.
            </p>
            <div className="model-selection-actions">
              <SaveActions
                saveStatus={saveStatus}
                saveError={saveError}
                disabled={!dirty}
                onSave={handleSave}
              />
              {dirty && saveStatus !== 'saving' && (
                <button type="button" className="model-selection-discard-btn" onClick={handleDiscard}>
                  Discard changes
                </button>
              )}
            </div>
          </div>
          <aside className="model-selection-side">
            <h5 className="model-selection-side-title">
              Menu preview{dirty ? ' (unsaved)' : ''}
            </h5>
            <ModelMenuPreview rows={data.models} drafts={drafts} />
          </aside>
        </div>
      )}
    </div>
  );
}

function GroupRows({
  label,
  rows,
  drafts,
  slotOptions,
  maxDescriptorLength,
  busy,
  onSlot,
  onChange,
}: {
  label: string;
  rows: ModelSelectionRow[];
  drafts: Record<string, Draft>;
  slotOptions: number[];
  maxDescriptorLength: number;
  busy: boolean;
  onSlot: (modelId: string, slot: number | null) => void;
  onChange: (modelId: string, patch: Partial<Draft>) => void;
}) {
  return (
    <>
      <tr className="model-selection-group-row">
        <th colSpan={5} scope="rowgroup">{label}</th>
      </tr>
      {rows.map((row) => {
        const draft = drafts[row.id];
        return (
          <tr key={row.id} className={`model-selection-row${row.available ? '' : ' unavailable'}`}>
            <td className="model-selection-col-model">
              <div className="model-selection-model-name">
                <span className="model-selection-model-name-text">{row.display_name}</span>
                {!row.available && row.unavailable_reason && (
                  <span
                    className={`model-selection-status-dot ${row.unavailable_reason}`}
                    role="img"
                    aria-label={UNAVAILABLE_LABELS[row.unavailable_reason]}
                    title={`${UNAVAILABLE_LABELS[row.unavailable_reason]} — hidden from users until the provider works (see Inference Providers)`}
                  />
                )}
              </div>
              <div className="model-selection-model-id" title={row.id}>{row.wire_id}</div>
            </td>
            <td className="model-selection-col-slot">
              <select
                className="model-selection-slot-select"
                value={draft.slot ?? ''}
                disabled={busy}
                aria-label={`Top-level slot for ${row.display_name}`}
                onChange={(e) => onSlot(row.id, e.target.value === '' ? null : Number(e.target.value))}
              >
                <option value="">—</option>
                {slotOptions.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </td>
            <td className="model-selection-col-descriptor">
              <input
                type="text"
                className="svc-cred-input model-selection-descriptor-input"
                value={draft.descriptor}
                maxLength={maxDescriptorLength}
                disabled={busy}
                placeholder={draft.slot === null ? 'Shown when slotted' : 'e.g. Smart ($$$)'}
                aria-label={`Descriptor for ${row.display_name}`}
                onChange={(e) => onChange(row.id, { descriptor: e.target.value })}
              />
            </td>
            <td className="model-selection-col-check">
              <input
                type="checkbox"
                checked={draft.allowPrivate}
                disabled={busy}
                aria-label={`Allow ${row.display_name} in private conversations`}
                onChange={(e) => onChange(row.id, { allowPrivate: e.target.checked })}
              />
            </td>
            <td className="model-selection-col-check">
              <input
                type="checkbox"
                checked={draft.allowPublic}
                disabled={busy}
                aria-label={`Allow ${row.display_name} in public conversations`}
                onChange={(e) => onChange(row.id, { allowPublic: e.target.checked })}
              />
            </td>
          </tr>
        );
      })}
    </>
  );
}
