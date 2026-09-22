/**
 * OAuth popup window utility.
 * Opens OAuth flows in a centered popup window instead of navigating the main window.
 */

/**
 * Open an OAuth flow in a centered popup window.
 * Returns the window reference (or null if popup was blocked).
 */
export function openOAuthPopup(url: string): Window | null {
  const width = 600;
  const height = 700;
  const left = window.screenX + (window.outerWidth - width) / 2;
  const top = window.screenY + (window.outerHeight - height) / 2;

  const popup = window.open(
    url,
    'oauth_popup',
    `width=${width},height=${height},left=${left},top=${top},toolbar=no,menubar=no,scrollbars=yes,resizable=yes`
  );

  return popup;
}
