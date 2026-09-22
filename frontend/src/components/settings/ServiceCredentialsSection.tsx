import { useState, useEffect, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import { fetchServiceCredentials, updateServiceCredentials } from '../../api/client';
import type {
  CredentialFieldSchema,
  ServiceCredentialDetail,
} from '../../api/types';
import './ServiceCredentialsSection.css';

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

export function secretPlaceholder(isSet: boolean, hint: string): string {
  return isSet ? 'Leave blank to keep the stored value' : hint;
}

/**
 * The subset of a credential/provider status the shared card chrome needs;
 * satisfied structurally by both ServiceCredentialDetail and the inference
 * provider statuses.
 */
export interface CredentialCardStatus {
  label: string;
  configured: boolean;
  source: 'store' | 'legacy' | null;
}

/** Card chrome shared by every service: header, badge, legacy note, body. */
export function CredentialCard({
  fallbackLabel,
  loading,
  loadError,
  detail,
  children,
}: {
  fallbackLabel: string;
  loading: boolean;
  loadError: string;
  detail: CredentialCardStatus | null;
  children: ReactNode;
}) {
  return (
    <div className="svc-cred-card">
      <div className="svc-cred-card-header">
        <h4>{detail?.label ?? fallbackLabel}</h4>
        {!loading && !loadError && (
          <span className={`svc-cred-badge ${detail?.configured ? 'configured' : 'unconfigured'}`}>
            {detail?.configured ? 'Configured' : 'Not configured'}
          </span>
        )}
      </div>
      {loading ? (
        <div className="settings-loading">Loading...</div>
      ) : loadError ? (
        <div className="svc-cred-load-error">{loadError}</div>
      ) : (
        <>
          {detail?.source === 'legacy' && (
            <p className="svc-cred-legacy-note">
              Currently read from a legacy credentials file on the server.
              Saving here moves the credentials to the per-service store,
              which takes precedence from then on.
            </p>
          )}
          {children}
        </>
      )}
    </div>
  );
}

export function CredentialField({
  id,
  label,
  value,
  onChange,
  placeholder,
  secret = false,
  optional = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  secret?: boolean;
  optional?: boolean;
}) {
  return (
    <div className="svc-cred-row">
      <label htmlFor={id} className="svc-cred-label">
        {label} {optional && <span className="svc-cred-optional">(optional)</span>}
      </label>
      <input
        id={id}
        className="svc-cred-input"
        type={secret ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={secret ? 'new-password' : 'off'}
      />
    </div>
  );
}

export function SaveActions({
  saveStatus,
  saveError,
  disabled,
  onSave,
}: {
  saveStatus: SaveStatus;
  saveError: string;
  disabled: boolean;
  onSave: () => void;
}) {
  return (
    <div className="settings-actions">
      <button
        className="settings-save-button"
        onClick={onSave}
        disabled={saveStatus === 'saving' || disabled}
      >
        {saveStatus === 'saving' ? 'Saving...' : 'Save'}
      </button>
      {saveStatus === 'saved' && (
        <span className="settings-save-status saved">Saved</span>
      )}
      {saveStatus === 'error' && (
        <span className="settings-save-status error">
          {saveError || 'Failed to save'}
        </span>
      )}
    </div>
  );
}

type FormValues = Record<string, string | boolean>;

/** Initial editable values from a fetched detail: secrets always reset to
 * empty (the server never returns them; empty means keep on save). */
function formToValues(detail: ServiceCredentialDetail): FormValues {
  const values: FormValues = {};
  for (const field of detail.fields) {
    if (field.type === 'bool') {
      values[field.key] = detail.credentials[field.key] === true;
    } else if (field.type === 'secret') {
      values[field.key] = '';
    } else {
      const raw = detail.credentials[field.key];
      values[field.key] = typeof raw === 'string' ? raw : '';
    }
  }
  return values;
}

function isFieldVisible(field: CredentialFieldSchema, values: FormValues): boolean {
  return !field.visible_if || values[field.visible_if] === true;
}

function isFieldRequired(field: CredentialFieldSchema, values: FormValues): boolean {
  if (field.required_if) return values[field.required_if] === true;
  return field.required;
}

/**
 * One service's credential card, rendered entirely from the backend's
 * CredentialFieldSchema list -- core services and plugins share this
 * component, so adding a service requires no frontend change.
 */
function GenericCredentialCard({ initialDetail }: { initialDetail: ServiceCredentialDetail }) {
  const [detail, setDetail] = useState(initialDetail);
  const [values, setValues] = useState<FormValues>(() => formToValues(initialDetail));
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [saveError, setSaveError] = useState('');
  const statusTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
    };
  }, []);

  const setValue = useCallback((key: string, value: string | boolean) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleSave = useCallback(async () => {
    setSaveStatus('saving');
    setSaveError('');
    try {
      const updated = await updateServiceCredentials(detail.service, values);
      setDetail(updated);
      setValues(formToValues(updated));
      setSaveStatus('saved');
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = window.setTimeout(() => setSaveStatus('idle'), 2000);
    } catch (error) {
      console.error(`Failed to save ${detail.service} credentials:`, error);
      setSaveStatus('error');
      setSaveError(error instanceof Error ? error.message : 'Failed to save');
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = window.setTimeout(() => setSaveStatus('idle'), 4000);
    }
  }, [detail.service, values]);

  // Required text fields must be filled before saving; required secrets are
  // left to the server (an empty value may legitimately keep a stored one).
  const missingRequired = detail.fields.some(
    (field) =>
      field.type !== 'bool' &&
      field.type !== 'secret' &&
      isFieldVisible(field, values) &&
      isFieldRequired(field, values) &&
      !String(values[field.key] ?? '').trim(),
  );

  return (
    <CredentialCard
      fallbackLabel={detail.label}
      loading={false}
      loadError=""
      detail={detail}
    >
      {detail.fields.map((field) => {
        if (!isFieldVisible(field, values)) return null;
        const id = `svc-cred-${detail.service}-${field.key}`;
        const optional = !isFieldRequired(field, values);
        if (field.type === 'bool') {
          return (
            <label key={field.key} className="svc-cred-toggle">
              <input
                type="checkbox"
                className="svc-cred-toggle-input"
                checked={values[field.key] === true}
                onChange={(e) => setValue(field.key, e.target.checked)}
              />
              <span className="svc-cred-toggle-track" aria-hidden="true">
                <span className="svc-cred-toggle-knob" />
              </span>
              <span className="svc-cred-toggle-text">{field.label}</span>
            </label>
          );
        }
        if (field.type === 'textarea') {
          return (
            <div key={field.key} className="svc-cred-row">
              <label htmlFor={id} className="svc-cred-label">
                {field.label}{' '}
                {optional && <span className="svc-cred-optional">(optional)</span>}
              </label>
              <textarea
                id={id}
                className="svc-cred-textarea"
                value={String(values[field.key] ?? '')}
                onChange={(e) => setValue(field.key, e.target.value)}
                placeholder={field.placeholder}
                rows={3}
                spellCheck={false}
              />
            </div>
          );
        }
        const secret = field.type === 'secret';
        return (
          <CredentialField
            key={field.key}
            id={id}
            label={field.label}
            value={String(values[field.key] ?? '')}
            onChange={(value) => setValue(field.key, value)}
            placeholder={
              secret
                ? secretPlaceholder(
                    detail.credentials[`${field.key}_set`] === true,
                    field.placeholder,
                  )
                : field.placeholder
            }
            secret={secret}
            optional={optional}
          />
        );
      })}
      <SaveActions
        saveStatus={saveStatus}
        saveError={saveError}
        disabled={missingRequired}
        onSave={handleSave}
      />
    </CredentialCard>
  );
}

/**
 * Admin-only editor for server-level upstream service credentials. The card
 * list and every card's fields come from GET /admin/service-credentials
 * (core services and loaded plugins alike) -- nothing here is per-service.
 * Credentials are persisted server-side in per-service files under the data
 * directory. Secret fields are write-only: the server never returns them,
 * and leaving a field blank keeps the stored value.
 */
export function ServiceCredentialsSection() {
  const [services, setServices] = useState<ServiceCredentialDetail[] | null>(null);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    const load = async () => {
      try {
        const response = await fetchServiceCredentials();
        setServices(response.services);
      } catch (error) {
        console.error('Failed to load service credentials:', error);
        setLoadError(error instanceof Error ? error.message : 'Failed to load');
      }
    };
    load();
  }, []);

  return (
    <div className="settings-section">
      <h3>Service Credentials</h3>
      <p className="settings-description">
        Server-level credentials for upstream API integrations. These apply to
        all users and are stored in per-service files in the server's data
        directory. Admin only.
      </p>
      {loadError ? (
        <div className="svc-cred-load-error">{loadError}</div>
      ) : services === null ? (
        <div className="settings-loading">Loading...</div>
      ) : (
        services.map((detail) => (
          <GenericCredentialCard key={detail.service} initialDetail={detail} />
        ))
      )}
    </div>
  );
}
