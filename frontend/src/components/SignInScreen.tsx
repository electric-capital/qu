import { useState, useEffect, useCallback } from 'react';
import './SignInScreen.css';
import { useConversationContext } from '../contexts/ConversationContext';

interface DevAccount {
  email: string;
  name: string;
  is_admin: boolean;
}

export function SignInScreen() {
  const { appName, isDevMode, loginRestriction } = useConversationContext();
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [googleUnavailable, setGoogleUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [devEmail, setDevEmail] = useState('');
  const [devLoginLoading, setDevLoginLoading] = useState(false);
  const [devAccounts, setDevAccounts] = useState<DevAccount[]>([]);

  useEffect(() => {
    const fetchLoginUrl = async () => {
      try {
        const response = await fetch('/auth/login-url', { credentials: 'include' });
        if (response.ok) {
          const data = await response.json();
          setAuthUrl(data.auth_url);
        } else {
          // In local mode Google OAuth is typically unconfigured (500 from
          // the missing credentials file) — that's expected, not an error.
          setGoogleUnavailable(true);
        }
      } catch {
        setGoogleUnavailable(true);
      }
    };
    fetchLoginUrl();
  }, []);

  // Local mode: fetch the canned-account roster for one-click login.
  useEffect(() => {
    if (!isDevMode) return;
    const fetchAccounts = async () => {
      try {
        const response = await fetch('/auth/dev-accounts', { credentials: 'include' });
        if (response.ok) {
          const data = await response.json();
          setDevAccounts(data.accounts ?? []);
        }
      } catch {
        // Roster fetch failed; the free-text email form still works.
      }
    };
    fetchAccounts();
  }, [isDevMode]);

  const devLogin = useCallback(async (email: string) => {
    if (!email.trim()) return;
    setDevLoginLoading(true);
    setError(null);
    try {
      const response = await fetch('/auth/dev-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email: email.trim() }),
      });
      if (response.ok) {
        window.location.reload();
      } else {
        const data = await response.json();
        setError(typeof data.detail === 'string' ? data.detail : 'Login failed');
        setDevLoginLoading(false);
      }
    } catch {
      setError('Failed to connect to server.');
      setDevLoginLoading(false);
    }
  }, []);

  const handleDevLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    await devLogin(devEmail);
  };

  return (
    <div className="sign-in-screen">
      <div className="sign-in-card">
        <h1 className="sign-in-title">{appName}</h1>
        <p className="sign-in-subtitle">AI-powered assistant</p>
        {error && <p className="sign-in-error">{error}</p>}
        {authUrl ? (
          <a href={authUrl} className="sign-in-button">
            Sign in with Google
          </a>
        ) : googleUnavailable ? (
          isDevMode
            ? <p className="sign-in-loading">Google sign-in not configured (fine in local mode)</p>
            : <p className="sign-in-error">Failed to initialize login. Please try again.</p>
        ) : (
          <p className="sign-in-loading">Loading...</p>
        )}
        {isDevMode && (
          <div className="dev-login-section">
            <div className="dev-login-divider">
              <span>{devAccounts.length > 0 ? 'pick an account' : 'or'}</span>
            </div>
            {devAccounts.length > 0 && (
              <div className="dev-accounts-list">
                {devAccounts.map((account) => (
                  <button
                    key={account.email}
                    className="dev-account-button"
                    disabled={devLoginLoading}
                    onClick={() => devLogin(account.email)}
                  >
                    <span className="dev-account-name">
                      {account.name || account.email}
                      {account.is_admin && <span className="dev-account-admin-badge">admin</span>}
                    </span>
                    <span className="dev-account-email">{account.email}</span>
                  </button>
                ))}
              </div>
            )}
            <form onSubmit={handleDevLogin} className="dev-login-form">
              <input
                type="email"
                value={devEmail}
                onChange={(e) => setDevEmail(e.target.value)}
                placeholder={devAccounts.length > 0 ? 'or any other email address' : 'Enter email address'}
                className="dev-login-input"
                required
              />
              <button type="submit" disabled={devLoginLoading} className="dev-login-button">
                {devLoginLoading ? 'Logging in...' : 'Dev Login'}
              </button>
            </form>
            <p className="dev-login-note">Local mode: any email, no Google account needed</p>
          </div>
        )}
        {!isDevMode && (
          <p className="sign-in-note">Access restricted to {loginRestriction}.</p>
        )}
      </div>
    </div>
  );
}
