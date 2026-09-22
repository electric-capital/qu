/**
 * Desktop notification service for alerting users when the browser tab is not focused.
 *
 * Fires browser Notification API alerts for two events:
 * 1. Stream completion (model finishes answering)
 * 2. New action request creation
 *
 * Follows the singleton service pattern used by requestEvents.ts --
 * no React dependency, safe to call from anywhere.
 */

const NOTIFICATION_AUTO_CLOSE_MS = 7000;

/**
 * Request notification permission from the browser.
 * Safe to call multiple times -- the browser only shows the prompt once.
 * Subsequent calls are no-ops that resolve immediately.
 */
export function requestNotificationPermission(): void {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

/**
 * Send a desktop notification if permission is granted and the tab is not focused.
 * Returns the Notification instance, or null if the notification was not sent.
 */
export function sendDesktopNotification(
  title: string,
  options?: NotificationOptions,
): Notification | null {
  // Guard: browser support
  if (!('Notification' in window)) return null;

  // Guard: permission not granted
  if (Notification.permission !== 'granted') return null;

  // Guard: tab is focused -- no need to notify
  if (!document.hidden) return null;

  const notification = new Notification(title, options);

  // Click brings the Quest tab to the foreground
  notification.onclick = () => {
    window.focus();
    notification.close();
  };

  // Auto-close after a few seconds
  setTimeout(() => {
    notification.close();
  }, NOTIFICATION_AUTO_CLOSE_MS);

  return notification;
}
