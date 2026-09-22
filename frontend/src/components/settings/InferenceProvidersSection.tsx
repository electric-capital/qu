import { useState, useEffect, useCallback, useRef } from 'react';
import { Check, Loader2, X } from 'lucide-react';
import {
  fetchInferenceProviders,
  testInferenceModel,
  updateInferenceProviderKey,
} from '../../api/client';
import type {
  ApiKeyProviderStatus,
  InferenceModelInfo,
  InferenceProviderStatus,
  VertexProviderStatus,
  VertexSectionInfo,
} from '../../api/types';
import {
  CredentialCard,
  CredentialField,
  SaveActions,
  secretPlaceholder,
} from './ServiceCredentialsSection';
import type { SaveStatus } from './ServiceCredentialsSection';
import './ServiceCredentialsSection.css';
import './InferenceProvidersSection.css';

const CREDENTIALS_SOURCE_LABELS: Record<string, string> = {
  env: 'Service-account key file (GOOGLE_APPLICATION_CREDENTIALS)',
  gcloud_adc: 'gcloud Application Default Credentials',
};

const PROJECT_SOURCE_LABELS: Record<string, string> = {
  env: 'environment variable',
  server_config: 'server_config.json',
  anthropic_fallback: 'inherited from the Claude on Vertex project',
};

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="inf-prov-info-row">
      <span className="inf-prov-info-label">{label}</span>
      <span className={`inf-prov-info-value${mono ? ' mono' : ''}`}>{value}</span>
    </div>
  );
}

function VertexSectionRows({ title, section }: { title: string; section: VertexSectionInfo }) {
  return (
    <div className="inf-prov-subsection">
      <h5 className="inf-prov-subsection-title">{title}</h5>
      {section.configured ? (
        <>
          <InfoRow
            label="Project"
            value={
              section.project_source
                ? `${section.vertex_project_id} (${PROJECT_SOURCE_LABELS[section.project_source]})`
                : section.vertex_project_id
            }
            mono
          />
          <InfoRow label="Region" value={section.vertex_region} mono />
        </>
      ) : (
        <p className="inf-prov-unconfigured-note">
          No Vertex project configured — these models are hidden from the
          model picker.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-model health state (server-stored, recheckable)
// ---------------------------------------------------------------------------

type ModelCheckState =
  | { status: 'idle' }
  | { status: 'checking' }
  | { status: 'ok'; checkedAt: string }
  | { status: 'error'; error: string; checkedAt: string };

const IDLE_CHECK: ModelCheckState = { status: 'idle' };

/** The stored server verdict for a model, as row state. */
function storedState(model: InferenceModelInfo): ModelCheckState {
  if (!model.status) return IDLE_CHECK;
  return model.status.ok
    ? { status: 'ok', checkedAt: model.status.checked_at }
    : {
        status: 'error',
        error: model.status.error ?? 'Check failed',
        checkedAt: model.status.checked_at,
      };
}

/**
 * Row state for a set of models: the server-stored verdict (from the startup
 * sweep) until a recheck is triggered here, then the live/local result --
 * rechecks also update the server-global store, so what this shows is what
 * the rest of the app will eventually see.
 */
function useModelChecks() {
  const [overrides, setOverrides] = useState<Record<string, ModelCheckState>>({});

  const runCheck = useCallback(async (modelId: string) => {
    setOverrides((prev) => ({ ...prev, [modelId]: { status: 'checking' } }));
    let next: ModelCheckState;
    try {
      const result = await testInferenceModel(modelId);
      next = result.ok
        ? { status: 'ok', checkedAt: result.checked_at }
        : {
            status: 'error',
            error: result.error ?? 'Check failed',
            checkedAt: result.checked_at,
          };
    } catch (error) {
      console.error(`Model health check failed for ${modelId}:`, error);
      next = {
        status: 'error',
        error: error instanceof Error ? error.message : 'Check failed',
        checkedAt: new Date().toISOString(),
      };
    }
    setOverrides((prev) => ({ ...prev, [modelId]: next }));
  }, []);

  const runAll = useCallback(
    (models: InferenceModelInfo[]) => {
      models.forEach((model) => runCheck(model.id));
    },
    [runCheck],
  );

  const stateFor = useCallback(
    (model: InferenceModelInfo): ModelCheckState =>
      overrides[model.id] ?? storedState(model),
    [overrides],
  );

  const anyChecking = Object.values(overrides).some((s) => s.status === 'checking');
  return { stateFor, runCheck, runAll, anyChecking };
}

/** Checkbox-style status indicator: empty box → spinner → ✓ / ✕. */
function ModelCheckIndicator({ state }: { state: ModelCheckState }) {
  if (state.status === 'checking') {
    return <Loader2 size={14} className="inf-prov-model-spinner" aria-label="Checking" />;
  }
  const modifier = state.status === 'ok' ? ' ok' : state.status === 'error' ? ' error' : '';
  const title =
    state.status === 'idle'
      ? 'Not checked yet'
      : `Last checked ${new Date(state.checkedAt).toLocaleString()}`;
  return (
    <span className={`inf-prov-model-box${modifier}`} title={title}>
      {state.status === 'ok' && <Check size={11} strokeWidth={3} />}
      {state.status === 'error' && <X size={11} strokeWidth={3} />}
    </span>
  );
}

function ModelRow({
  model,
  state,
  onCheck,
  checkable,
}: {
  model: InferenceModelInfo;
  state: ModelCheckState;
  onCheck: () => void;
  checkable: boolean;
}) {
  return (
    <div className="inf-prov-model-row">
      <div className="inf-prov-model-line">
        <ModelCheckIndicator state={state} />
        <span className="inf-prov-model-name" title={model.id}>
          {model.display_name}
        </span>
        {checkable && (
          <button
            className="inf-prov-model-check-btn"
            onClick={onCheck}
            disabled={state.status === 'checking'}
          >
            {state.status === 'idle' || state.status === 'checking' ? 'Check' : 'Recheck'}
          </button>
        )}
      </div>
      {state.status === 'error' && <p className="inf-prov-model-error">{state.error}</p>}
    </div>
  );
}

interface ModelGroup {
  // null renders the group without its own heading (single-family providers)
  title: string | null;
  configured: boolean;
  models: InferenceModelInfo[];
}

/**
 * Right-hand Models panel: the current server-stored health verdict per
 * model (the server checks every configured model at startup) with Recheck
 * buttons that re-run the live check and update the server-global store.
 */
function ModelsPanel({ groups }: { groups: ModelGroup[] }) {
  const { stateFor, runCheck, runAll, anyChecking } = useModelChecks();
  const nonEmpty = groups.filter((group) => group.models.length > 0);
  const checkable = nonEmpty.filter((g) => g.configured).flatMap((g) => g.models);
  if (nonEmpty.length === 0) return null;

  return (
    <div className="inf-prov-models">
      <div className="inf-prov-models-header">
        <h5 className="inf-prov-subsection-title">Models</h5>
        <button
          className="inf-prov-model-check-btn"
          onClick={() => runAll(checkable)}
          disabled={anyChecking || checkable.length === 0}
        >
          Recheck all
        </button>
      </div>
      <p className="inf-prov-models-note">
        Every configured model is health-checked at server startup with a
        minimal real inference call — this catches models that are not
        enabled in Vertex Model Garden as well as quota and permission
        problems. Recheck re-runs a check and updates the stored result.
      </p>
      {nonEmpty.map((group, index) => (
        <div key={group.title ?? index} className="inf-prov-subsection">
          {group.title && <h5 className="inf-prov-subsection-title">{group.title}</h5>}
          {group.models.map((model) => (
            <ModelRow
              key={model.id}
              model={model}
              state={stateFor(model)}
              onCheck={() => runCheck(model.id)}
              checkable={group.configured}
            />
          ))}
          {!group.configured && (
            <p className="inf-prov-unconfigured-note">
              Not configured — checks unavailable.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

/** Read-only card showing the Vertex AI setup detected from the environment. */
function VertexProviderCard({ status }: { status: VertexProviderStatus }) {
  const creds = status.detail.credentials;
  return (
    <CredentialCard
      fallbackLabel={status.label}
      loading={false}
      loadError=""
      detail={{ label: status.label, configured: status.configured, source: null }}
    >
      <div className="inf-prov-columns">
        <div className="inf-prov-main">
          <p className="inf-prov-detected-note">
            Detected from the server environment and server_config.json — read-only.
          </p>
          <div className="inf-prov-subsection">
            <h5 className="inf-prov-subsection-title">Credentials</h5>
            {creds.source ? (
              <>
                <InfoRow label="Source" value={CREDENTIALS_SOURCE_LABELS[creds.source]} />
                {creds.key_path && <InfoRow label="File" value={creds.key_path} mono />}
                {creds.service_account_email && (
                  <InfoRow label="Service account" value={creds.service_account_email} mono />
                )}
                {creds.project_id && <InfoRow label="Key project" value={creds.project_id} mono />}
                {creds.problem && <p className="inf-prov-problem">{creds.problem}</p>}
              </>
            ) : (
              <p className="inf-prov-unconfigured-note">
                No Google credentials detected (GOOGLE_APPLICATION_CREDENTIALS or
                gcloud Application Default Credentials). On Google Cloud VMs the
                instance service account may still apply.
              </p>
            )}
          </div>
          <VertexSectionRows title="Claude on Vertex" section={status.detail.anthropic} />
          <VertexSectionRows title="Gemini on Vertex" section={status.detail.gemini_vertex} />
        </div>
        <ModelsPanel
          groups={[
            {
              title: 'Claude on Vertex',
              configured: status.detail.anthropic.configured,
              models: status.models.filter((m) => m.family === 'anthropic'),
            },
            {
              title: 'Gemini on Vertex',
              configured: status.detail.gemini_vertex.configured,
              models: status.models.filter((m) => m.family === 'gemini_vertex'),
            },
          ]}
        />
      </div>
    </CredentialCard>
  );
}

/** Editable card for an API-key provider (none registered today; kept for
 * future direct-API providers -- the backend registry drives the list). */
function ApiKeyProviderCard({ status: initial }: { status: ApiKeyProviderStatus }) {
  const [status, setStatus] = useState(initial);
  const [apiKey, setApiKey] = useState('');
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [saveError, setSaveError] = useState('');
  const statusTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
    };
  }, []);

  const handleSave = useCallback(async () => {
    setSaveStatus('saving');
    setSaveError('');
    try {
      const updated = await updateInferenceProviderKey(status.provider, { api_key: apiKey });
      setStatus(updated);
      setApiKey('');
      setSaveStatus('saved');
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = window.setTimeout(() => setSaveStatus('idle'), 2000);
    } catch (error) {
      console.error(`Failed to save ${status.provider} API key:`, error);
      setSaveStatus('error');
      setSaveError(error instanceof Error ? error.message : 'Failed to save');
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = window.setTimeout(() => setSaveStatus('idle'), 4000);
    }
  }, [status.provider, apiKey]);

  return (
    <CredentialCard
      fallbackLabel={status.label}
      loading={false}
      loadError=""
      detail={status}
    >
      <div className="inf-prov-columns">
        <div className="inf-prov-main">
          <CredentialField
            id={`inf-prov-${status.provider}-api-key`}
            label="API key"
            value={apiKey}
            onChange={setApiKey}
            placeholder={secretPlaceholder(status.credentials.api_key_set, status.hint)}
            secret
          />
          <SaveActions
            saveStatus={saveStatus}
            saveError={saveError}
            disabled={!apiKey.trim() && !status.configured}
            onSave={handleSave}
          />
        </div>
        <ModelsPanel
          groups={[{ title: null, configured: status.configured, models: status.models }]}
        />
      </div>
    </CredentialCard>
  );
}

/**
 * Admin-only panel for LLM inference provider configuration. Vertex AI is
 * shown read-only (its credentials and project config are detected from the
 * server environment); API-key providers, when registered, are editable
 * here. Each card's Models panel shows the server-stored health verdict per
 * model — populated by the startup check sweep — with Recheck buttons that
 * re-run the live check and update the global store. The card list is driven
 * by the backend registry, so newly registered providers (e.g. a direct
 * Anthropic or OpenAI API) appear without frontend changes.
 */
export function InferenceProvidersSection() {
  const [providers, setProviders] = useState<InferenceProviderStatus[] | null>(null);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    const load = async () => {
      try {
        const response = await fetchInferenceProviders();
        setProviders(response.providers);
      } catch (error) {
        console.error('Failed to load inference providers:', error);
        setLoadError(error instanceof Error ? error.message : 'Failed to load');
      }
    };
    load();
  }, []);

  return (
    <div className="settings-section">
      <h3>Inference Providers</h3>
      <p className="settings-description">
        LLM backends used to run conversations. These apply to all users;
        models without configured credentials are hidden from the model
        picker. Admin only.
      </p>
      {loadError ? (
        <div className="svc-cred-load-error">{loadError}</div>
      ) : providers === null ? (
        <div className="settings-loading">Loading...</div>
      ) : (
        <div className="inf-prov-cards">
          {providers.map((provider) =>
            provider.kind === 'detected' ? (
              <VertexProviderCard key={provider.provider} status={provider} />
            ) : (
              <ApiKeyProviderCard key={provider.provider} status={provider} />
            ),
          )}
        </div>
      )}
    </div>
  );
}
