import { useCallback, useEffect, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { createPasswordLink, fetchAdminSignIn, switchToGoogleSignIn } from '../../api/client';
import type { AdminSignInStatus, PasswordLinkResult } from '../../api/types';
import './SignInSettings.css';

/**
 * Settings > Sign-in (admin): the deployment's sign-in method, set-password
 * links for inviting users / resetting passwords under password sign-in,
 * and the one-way switch to Google sign-in (accounts are keyed by email, so
 * everyone keeps their account).
 */
export function SignInSection() {
  const [status, setStatus] = useState<AdminSignInStatus | null>(null);
  const [loadError, setLoadError] = useState('');
  const [email, setEmail] = useState('');
  const [sendEmail, setSendEmail] = useState(true);
  const [creating, setCreating] = useState(false);
  const [linkError, setLinkError] = useState('');
  const [link, setLink] = useState<PasswordLinkResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState('');

  useEffect(() => {
    fetchAdminSignIn()
      .then(setStatus)
      .catch((err) => setLoadError(err instanceof Error ? err.message : 'Failed to load sign-in settings.'));
  }, []);

  const handleCreateLink = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || creating) return;
    setCreating(true);
    setLinkError('');
    setLink(null);
    try {
      const result = await createPasswordLink(email.trim(), sendEmail && !!status?.smtp_configured);
      setLink(result);
      setCopied(false);
      setEmail('');
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : 'Could not create the link.');
    } finally {
      setCreating(false);
    }
  }, [email, creating, sendEmail, status]);

  const handleCopy = useCallback(async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy link:', err);
    }
  }, [link]);

  const handleSwitch = useCallback(async () => {
    if (!confirm(
      'Switch this deployment to Google sign-in?\n\n' +
      'Everyone keeps their account and signs in with the Google account of the same ' +
      'email address. Password sign-in stops working immediately and everyone ' +
      'signed in with a password (including you) is signed out.\n\n' +
      'Make sure the Google OAuth client lists this deployment\'s /auth/callback ' +
      'redirect URI first.'
    )) {
      return;
    }
    setSwitching(true);
    setSwitchError('');
    try {
      await switchToGoogleSignIn();
      window.location.href = '/';
    } catch (err) {
      setSwitchError(err instanceof Error ? err.message : 'Could not switch the sign-in method.');
      setSwitching(false);
    }
  }, []);

  if (loadError) {
    return (
      <div className="settings-section">
        <h3>Sign-in</h3>
        <p className="signin-error">{loadError}</p>
      </div>
    );
  }
  if (!status) {
    return (
      <div className="settings-section">
        <h3>Sign-in</h3>
        <div className="settings-loading">Loading...</div>
      </div>
    );
  }

  if (status.login_method === 'google') {
    return (
      <div className="settings-section">
        <h3>Sign-in</h3>
        <p className="signin-status">Users sign in with <strong>Google</strong>.</p>
        <p className="settings-description">
          Who may sign in is set by <code>allowed_login_domain</code> and{' '}
          <code>allowed_login_emails</code> in <code>server_config.json</code>. To go back to
          email and password sign-in, set <code>"login_method": "password"</code> there.
        </p>
      </div>
    );
  }

  return (
    <div className="settings-section">
      <h3>Sign-in</h3>
      <p className="signin-status">Users sign in with <strong>email and password</strong>.</p>
      <p className="settings-description">
        {status.smtp_configured
          ? 'Outgoing email is configured: users can reset their own password, and anyone on the allowed domain or list can create an account from the sign-in screen.'
          : 'Outgoing email is not configured, so users cannot reset their own password or sign up. Create links for them below, or configure "Outgoing email (SMTP)" under Service Credentials.'}
      </p>

      <h4 className="signin-subheading">Invite a user or reset a password</h4>
      <p className="settings-description">
        Creates a one-time link to set a password: it creates the account for a new
        address, or replaces the password of an existing one. Links expire after 7 days.
        Addresses outside the allowed domain are added to the allowed list.
      </p>
      <form onSubmit={handleCreateLink}>
        <div className="signin-row">
          <input
            type="email"
            className="signin-input"
            placeholder="person@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <button type="submit" className="signin-button" disabled={!email.trim() || creating}>
            {creating ? 'Creating...' : 'Create link'}
          </button>
        </div>
        {status.smtp_configured && (
          <label className="signin-checkbox">
            <input type="checkbox" checked={sendEmail} onChange={(e) => setSendEmail(e.target.checked)} />
            Also email the link
          </label>
        )}
      </form>
      {linkError && <p className="signin-error">{linkError}</p>}
      {link && (
        <div className="signin-link-box">
          <div className="signin-status">
            {link.account_exists ? 'Password reset link' : 'Invite link'} for <strong>{link.email}</strong>
          </div>
          {link.emailed && <p className="signin-success">Emailed to {link.email}.</p>}
          {link.email_error && <p className="signin-warning">Not emailed: {link.email_error}</p>}
          {link.added_to_allowed_emails && (
            <p className="signin-warning">{link.email} was added to allowed_login_emails.</p>
          )}
          <div className="signin-link-row">
            <code className="signin-link">{link.url}</code>
            <button
              type="button"
              className={`signin-button signin-copy-btn${copied ? ' copied' : ''}`}
              onClick={handleCopy}
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
      )}

      <h4 className="signin-subheading">Switch to Google sign-in</h4>
      <p className="settings-description">
        Everyone keeps their account (matched by email address) and conversations. After
        the switch, password sign-in is disabled.
      </p>
      {!status.google_oauth_configured && (
        <p className="signin-warning">
          Configure the Google OAuth client under Service Credentials first.
        </p>
      )}
      <button
        type="button"
        className="signin-button"
        onClick={handleSwitch}
        disabled={!status.google_oauth_configured || switching}
      >
        {switching ? 'Switching...' : 'Switch to Google sign-in'}
      </button>
      {switchError && <p className="signin-error">{switchError}</p>}
    </div>
  );
}
