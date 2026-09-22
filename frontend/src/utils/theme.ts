/**
 * Settings > Appearance colour-scheme preference.
 *
 * "auto" follows the OS (no attribute on <html>); "light" / "dark" force a
 * scheme by setting <html data-theme>, which the build-time
 * themeOverridePlugin.ts teaches every `prefers-color-scheme` media block to
 * honour. The preference is stored server-side in users.settings.theme (the
 * source of truth, so it follows the user across devices) and cached in
 * localStorage so index.html can apply it before the first paint.
 */

export type ThemePreference = 'light' | 'dark' | 'auto';

export const THEME_OPTIONS: ThemePreference[] = ['light', 'dark', 'auto'];

// Must match the inline boot script in index.html.
export const THEME_STORAGE_KEY = 'quest_theme';

export function normalizeTheme(value: unknown): ThemePreference {
  return value === 'light' || value === 'dark' ? value : 'auto';
}

/** Set (or clear, for auto) the data-theme attribute on <html>. */
export function applyTheme(theme: ThemePreference): void {
  const root = document.documentElement;
  if (theme === 'auto') {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', theme);
  }
}

/** Cache the preference for the next page load's pre-paint boot script. */
export function cacheTheme(theme: ThemePreference): void {
  try {
    if (theme === 'auto') {
      localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    }
  } catch {
    // Storage may be unavailable (private mode, blocked site data); the
    // server value still applies on hydration.
  }
}

export function readCachedTheme(): ThemePreference {
  try {
    return normalizeTheme(localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return 'auto';
  }
}
