/**
 * Settings > Appearance colour THEME (as opposed to the light/dark/auto
 * colour SCHEME in theme.ts).
 *
 * A colour theme swaps the theme-controlled design tokens -- accent family
 * plus the chat ground and the nav/composer/panel ground -- for both colour
 * schemes at once. The palettes live in src/themes.css, keyed by the
 * `<html data-color-theme>` attribute this module owns. The choice is stored
 * server-side in users.settings.color_theme (source of truth, follows the
 * user across devices) and cached in localStorage so index.html can apply it
 * before the first paint.
 *
 * Keep COLOR_THEMES in step with src/themes.css and the server allow-list
 * COLOR_THEME_CHOICES in chat/routes/user.py.
 */

export type ColorThemeId = 'prototype' | 'electric-blue' | 'alloy' | 'recall';

export interface ColorThemeSpec {
  id: ColorThemeId;
  label: string;
  description: string;
  /** Swatch colours for the picker tile: [dark, light] per role. */
  swatch: {
    accent: [string, string];
    surface: [string, string];
    surfaceRaised: [string, string];
  };
}

export const COLOR_THEMES: ColorThemeSpec[] = [
  {
    id: 'prototype',
    label: 'Prototype',
    description: 'The original Quest look: indigo accent on neutral greys.',
    swatch: {
      accent: ['#646cff', '#646cff'],
      surface: ['#1f1f1f', '#ffffff'],
      surfaceRaised: ['#282828', '#ffffff'],
    },
  },
  {
    id: 'electric-blue',
    label: 'Electric',
    description: 'Electric blue accent on cool grays.',
    swatch: {
      accent: ['#00bbf2', '#00bbf2'],
      surface: ['#161616', '#fcfcfc'],
      surfaceRaised: ['#202020', '#ffffff'],
    },
  },
  {
    id: 'alloy',
    label: 'Alloy',
    description: 'Forest green accent on warm cream.',
    swatch: {
      accent: ['#e2e5dd', '#326849'],
      surface: ['#0b1914', '#fffcec'],
      surfaceRaised: ['#11251b', '#f6f3e1'],
    },
  },
  {
    id: 'recall',
    label: 'Recall',
    description: 'Sage-mint accent on near-black.',
    swatch: {
      accent: ['#8db5ac', '#4f7d72'],
      surface: ['#111111', '#f5f8f7'],
      surfaceRaised: ['#1a1d1c', '#ffffff'],
    },
  },
];

export const DEFAULT_COLOR_THEME: ColorThemeId = 'prototype';

// Must match the inline boot script in index.html.
export const COLOR_THEME_STORAGE_KEY = 'quest_color_theme';

const KNOWN_IDS = new Set<string>(COLOR_THEMES.map((t) => t.id));

export function normalizeColorTheme(value: unknown): ColorThemeId {
  return typeof value === 'string' && KNOWN_IDS.has(value)
    ? (value as ColorThemeId)
    : DEFAULT_COLOR_THEME;
}

/** Set <html data-color-theme> and keep the mobile browser chrome colour in step. */
export function applyColorTheme(theme: ColorThemeId): void {
  const root = document.documentElement;
  root.setAttribute('data-color-theme', theme);
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta) {
    const accent = getComputedStyle(root).getPropertyValue('--accent').trim();
    if (accent) meta.content = accent;
  }
}

/** Cache the choice for the next page load's pre-paint boot script. */
export function cacheColorTheme(theme: ColorThemeId): void {
  try {
    if (theme === DEFAULT_COLOR_THEME) {
      localStorage.removeItem(COLOR_THEME_STORAGE_KEY);
    } else {
      localStorage.setItem(COLOR_THEME_STORAGE_KEY, theme);
    }
  } catch {
    // Storage may be unavailable; the server value still applies on hydration.
  }
}

export function readCachedColorTheme(): ColorThemeId {
  try {
    return normalizeColorTheme(localStorage.getItem(COLOR_THEME_STORAGE_KEY));
  } catch {
    return DEFAULT_COLOR_THEME;
  }
}
