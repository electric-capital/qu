import { AdminOpsMenu } from './AdminOpsMenu';
import './UserInfoBar.css';

interface UserInfoBarProps {
  email: string | null;
  name: string | null;
  onSettingsClick: () => void;
}

export function UserInfoBar({ email, name, onSettingsClick }: UserInfoBarProps) {
  if (!email) return null;

  // Show name if available, otherwise show the part before @ in email
  const displayName = name || email.split('@')[0];

  return (
    <div className="user-info-bar">
      <div className="user-info">
        <div className="user-avatar">
          {displayName.charAt(0).toUpperCase()}
        </div>
        <div className="user-details">
          <div className="user-name">{displayName}</div>
          <div className="user-email">{email}</div>
        </div>
      </div>
      <div className="user-info-actions">
        {/* Admin-only wrench menu; renders nothing for regular users */}
        <AdminOpsMenu inline />
        <button
          className="settings-button"
          onClick={onSettingsClick}
          title="Settings"
          aria-label="Open settings"
        >
          {/* Gear icon - inline SVG to avoid dependencies */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3"></circle>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
          </svg>
        </button>
      </div>
    </div>
  );
}
