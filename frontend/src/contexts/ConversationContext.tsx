/**
 * React Context for conversation state management
 * Provides activeConversationId and session auth state to child components
 */

import React, { createContext, useContext, useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { checkSession } from '../utils/auth';
import { applyTheme, cacheTheme, normalizeTheme, readCachedTheme, type ThemePreference } from '../utils/theme';
import { applyColorTheme, cacheColorTheme, normalizeColorTheme, readCachedColorTheme, type ColorThemeId } from '../utils/colorTheme';
import { fetchGuides, fetchProjects, fetchVersion } from '../api/client';
import { DEPRECATED_MODEL_MAP, DEFAULT_MODEL_ID, isModelSelectableFor, resolveFallbackModel, setModelCatalog } from '../constants/models';
import type { ModelVisibility } from '../constants/models';
import { HOME_DRAFT_KEY } from '../constants/drafts';
import { persistentWebSocket } from '../services/PersistentWebSocket';
import type { ComposerAttachmentRef, Guide, Project } from '../api/types';

/** How often to poll for server version changes (ms). */
const VERSION_POLL_INTERVAL_MS = 60_000;

/**
 * Resolve a chain of (possibly null / stale / unknown) server-stored model
 * ids to a usable model id for a conversation visibility: the first
 * candidate that -- after the deprecated-id remap -- is in the catalog, not
 * deprecated, allowed by the admin for that visibility (Settings > Model
 * Selection) and credentialed (available_models from GET /app/api/config;
 * null while unknown) wins; otherwise the best offerable model for that
 * visibility (see resolveFallbackModel: Opus 4.8 when allowed + credentialed,
 * else the admin's first top-level pick, else the first offerable model in
 * catalog order). A stored pick that is deprecated (still runnable, but
 * hidden from the picker) also falls through, so new conversations never
 * start on a deprecated model. Single place the FE applies the fallback (the
 * backend /me returns the raw stored values).
 */
function resolveDefaultModel(
  candidates: (string | null | undefined)[],
  availableIds: string[] | null,
  visibility: ModelVisibility,
): string {
  for (const raw of candidates) {
    if (!raw) continue;
    const remapped = DEPRECATED_MODEL_MAP[raw] || raw;
    if (isModelSelectableFor(remapped, visibility, availableIds)) return remapped;
  }
  return resolveFallbackModel(availableIds, visibility);
}

/**
 * The stored "last-used" candidates for a visibility, from a GET /me payload.
 * Public composers try the public pick first and then the private one, so a
 * user's first public project starts on their usual model when the admin
 * allows it there; private composers only ever use the private pick.
 */
function defaultModelCandidates(
  userInfo: { default_model?: string | null; public_default_model?: string | null } | null,
  visibility: ModelVisibility,
): (string | null | undefined)[] {
  if (!userInfo) return [];
  return visibility === 'public'
    ? [userInfo.public_default_model, userInfo.default_model]
    : [userInfo.default_model];
}

// File browser state per conversation
interface FileBrowserState {
  path: string;
  history: string[];
  historyIndex: number;
}

export type LoginMethod = 'google' | 'password';

interface ConversationContextValue {
  activeConversationId: string | null;
  setActiveConversationId: (id: string | null) => void;
  isAuthenticated: boolean;
  isCheckingAuth: boolean;
  // App name: "DevQuest" in local mode, "Quest" in staging/production
  appName: string;
  // Whether running in local mode (QUEST_ENV=local; legacy alias "dev").
  // Enables the canned-account sign-in picker.
  isDevMode: boolean;
  // Human-readable sign-in restriction (from GET /app/api/config), e.g.
  // "@example.com accounts" or "approved accounts" on deployments with an
  // email whitelist. Defaults to the built-in company domain until the
  // config fetch resolves; shown on the sign-in screen.
  loginRestriction: string;
  // Active sign-in method (GET /app/api/config): "google" (Google OAuth) or
  // "password" (email + password accounts). Exactly one is active. null
  // until the config fetch resolves.
  loginMethod: LoginMethod | null;
  // Password sign-in only: whether the sign-in screen can offer self-service
  // sign-up / forgot-password (the server has outgoing email configured).
  passwordSelfService: boolean;
  // Whether the signed-in account has a sign-in password (GET /me); the
  // Settings > Password section asks for the current one when it does.
  hasPassword: boolean;
  setHasPassword: (value: boolean) => void;
  // Model IDs whose backend credentials are configured server-side (from
  // GET /app/api/config). null until the config fetch resolves; treat null
  // as "all models" so the picker doesn't flicker empty on load.
  availableModelIds: string[] | null;
  // Per-user "last-used" default conversation model, one per conversation
  // visibility: `private` (users.settings.default_model -- everything outside
  // a public project) and `public` (users.settings.public_default_model --
  // public-project conversations, whose admin allow-list differs). Sourced
  // ONLY from a fresh GET /me fetch (no localStorage); each falls back to the
  // best model offerable for its visibility (resolveFallbackModel). Re-read
  // on every new-composer mount AND every private/public context switch via
  // refreshDefaultModel(); written server-side only on the first send of a
  // new chat via persistDefaultModel(). `defaultModel` is the private one.
  defaultModel: string;
  defaultModels: Record<ModelVisibility, string>;
  setDefaultModel: (model: string, visibility?: ModelVisibility) => void;
  // Re-fetch the per-user defaults from the server (GET /me) and re-apply the
  // one for the given visibility (private when omitted). Called on every
  // fresh new-chat composer mount and whenever a composer switches between
  // the private and public contexts, for cross-tab correctness (no WS push).
  // Does not touch localStorage. Resolves to the freshly-resolved model id
  // (visibility-aware fallback when unset/disallowed/uncredentialed).
  refreshDefaultModel: (visibility?: ModelVisibility) => Promise<string>;
  // Best-effort PUT /settings { default_model } (private) or
  // { public_default_model } (public) persisting the user-level last-used
  // pick for that visibility. The ONLY server write of either; called solely
  // from the first-send paths.
  persistDefaultModel: (model: string, visibility?: ModelVisibility) => void;
  getModelForConversation: (conversationId: string) => string;
  setModelForConversation: (conversationId: string, model: string) => void;
  hydrateModelForConversation: (conversationId: string, model: string) => void;
  // Draft-safe model setter for the root home composer: updates only the
  // in-memory per-conversation model map (and the given visibility's
  // in-memory default for display, private when omitted). Does NOT PATCH the
  // server and does NOT persist the user default (the draft "conversation"
  // doesn't exist yet; the default is persisted on first send).
  setDraftModelForConversation: (conversationId: string, model: string, visibility?: ModelVisibility) => void;
  // File browser state per conversation
  getFileBrowserState: (conversationId: string) => FileBrowserState;
  setFileBrowserState: (conversationId: string, state: FileBrowserState) => void;
  // User info
  userEmail: string | null;
  userName: string | null;
  // Settings modal
  isSettingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
  settingsInitialSection: string | null;
  setSettingsInitialSection: (section: string | null) => void;
  // Google Services connection status (for new user flow)
  googleServicesConnected: boolean;
  // Whether user has any backend service connected (for auto-open settings decision)
  hasAnyServiceConnected: boolean;
  // Refresh connection status after OAuth popup completes
  refreshConnectionStatus: () => void;
  // Guide list (deprecated feature; consumed by the Settings Guides section
  // and the routine guide-override dropdowns)
  guides: Guide[];
  guidesLoaded: boolean;
  loadGuides: () => Promise<void>;
  // Provider locking state (lock model provider after first message)
  getLockedProvider: (conversationId: string) => string | null;
  lockConversationProvider: (conversationId: string, provider: string) => void;
  isProviderLocked: (conversationId: string) => boolean;
  // Project state
  projects: Project[];
  projectsLoaded: boolean;
  loadProjects: () => Promise<void>;
  activeProjectId: string | null;
  setActiveProjectId: (id: string | null) => void;
  // The project the Sidebar is currently drilled into (its per-project view).
  // Distinct from activeProjectId, which mirrors the URL and is nulled whenever
  // the URL is "/" -- e.g. right after drilling into an EMPTY project, which
  // shows the home composer. The home composer reads this to create its first
  // conversation inside the drilled project instead of as a standalone chat.
  drilledProjectId: string | null;
  setDrilledProjectId: (id: string | null) => void;
  // Pending routine message (set when a routine is invoked, consumed by ChatPanel)
  pendingRoutineMessage: { conversationId: string; prompt: string; guideId: string | null } | null;
  setPendingRoutineMessage: (msg: { conversationId: string; prompt: string; guideId: string | null } | null) => void;
  // Pending first message (set by the root HomeComposer, consumed by ChatPanel).
  // Carries the home-chosen model/skills/flags into the first send. Distinct
  // from pendingRoutineMessage because it must carry model + skillIds + flags.
  pendingFirstMessage: {
    conversationId: string;
    prompt: string;
    model: string;
    skillIds: string[];
    flags: string[];
    // Whether the chat was started inside a public project (the home
    // composer knows from the drilled project; ChatPanel only learns it
    // asynchronously), so the first send persists the right last-used pick.
    isPublicProject: boolean;
    // Workspace-relative names of generic files attached on the home screen,
    // uploaded into the new workspace before navigation; forwarded into the
    // first send so they appear in the triggering turn's metadata. Empty when
    // nothing was attached.
    attachedFilenames: string[];
    // Refs of clipboard images pasted on the home screen, uploaded via the
    // composer-attachments endpoint after the conversation was created;
    // forwarded as the first send's multimodal attachments. Empty when none.
    attachments: ComposerAttachmentRef[];
  } | null;
  setPendingFirstMessage: (msg: {
    conversationId: string;
    prompt: string;
    model: string;
    skillIds: string[];
    flags: string[];
    isPublicProject: boolean;
    attachedFilenames: string[];
    attachments: ComposerAttachmentRef[];
  } | null) => void;
  // Requests view state
  showRequestsView: boolean;
  setShowRequestsView: (show: boolean) => void;
  // Scroll-to-message state (used by search to scroll to a specific message)
  scrollToMessageIndex: number | null;
  setScrollToMessageIndex: (index: number | null) => void;
  // Per-conversation loaded skills state
  getQueuedSkillsForConversation: (conversationId: string) => string[];
  setQueuedSkillsForConversation: (conversationId: string, skillIds: string[]) => void;
  getLoadedSkillsForConversation: (conversationId: string) => string[];
  setLoadedSkillsForConversation: (conversationId: string, skillIds: string[]) => void;
  markSkillsAsLoaded: (conversationId: string, skillIds: string[]) => void;
  // Re-fetch GET /app/api/config and replace the model catalog +
  // available_models (used after an admin saves Settings > Model Selection
  // so the composer menu in this tab reflects the change without a reload).
  refreshModelCatalog: () => Promise<void>;
  // Admin state
  isAdmin: boolean;
  // Server-global admin feature gates that are currently on (enabled_features
  // from GET /app/api/me). Feature-gated composer flags are hidden when their
  // feature is absent from this list.
  enabledFeatures: string[];
  // Re-fetch enabled_features from GET /me (called after an admin flips a
  // gate in Settings > Features so their own composer reflects it without a
  // reload; other sessions pick it up on their next load).
  refreshEnabledFeatures: () => Promise<void>;
  // Settings > Appearance colour scheme ("light" / "dark" / "auto"). Boots
  // from the localStorage cache (already applied pre-paint by index.html),
  // then re-synced from users.settings.theme on GET /me hydration. setTheme
  // applies it immediately, caches it, and persists via PUT /settings.
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => Promise<void>;
  // Settings > Appearance colour THEME (palette: accent + grounds, e.g.
  // "prototype" / "electric-blue" / "alloy" / "recall"), same boot/hydrate/persist cycle as
  // `theme` against users.settings.color_theme and <html data-color-theme>.
  colorTheme: ColorThemeId;
  setColorTheme: (theme: ColorThemeId) => Promise<void>;
  // Impersonation state
  isImpersonating: boolean;
  impersonatorEmail: string | null;
  impersonatorName: string | null;
  // Version update detection
  updateAvailable: boolean;
}

const ConversationContext = createContext<ConversationContextValue | null>(null);

interface ConversationProviderProps {
  children: React.ReactNode;
}

/**
 * ConversationProvider - wraps the app and provides conversation context
 */
export function ConversationProvider({ children }: ConversationProviderProps) {
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  // Per-user default conversation model per visibility. No localStorage: the
  // authoritative values come from a fresh GET /me fetch (hydration + every
  // new-composer mount / context switch). Start both at the Opus-4.8
  // fallback as a pre-fetch placeholder.
  const [defaultModels, setDefaultModels] = useState<Record<ModelVisibility, string>>({
    private: DEFAULT_MODEL_ID,
    public: DEFAULT_MODEL_ID,
  });
  const defaultModel = defaultModels.private;
  const setDefaultModelState = useCallback((model: string, visibility: ModelVisibility = 'private') => {
    setDefaultModels((prev) => (prev[visibility] === model ? prev : { ...prev, [visibility]: model }));
  }, []);
  const [conversationModels, setConversationModels] = useState<Record<string, string>>({});
  const [fileBrowserStates, setFileBrowserStates] = useState<Record<string, FileBrowserState>>({});
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [userName, setUserName] = useState<string | null>(null);
  const [isSettingsOpen, setSettingsOpen] = useState(false);
  const [settingsInitialSection, setSettingsInitialSection] = useState<string | null>(null);
  const [googleServicesConnected, setGoogleServicesConnected] = useState(true); // Default true to avoid flash
  const [hasAnyServiceConnected, setHasAnyServiceConnected] = useState(true); // Default true to avoid flash

  // Guide list (deprecated feature; still listed for the Settings section and
  // the routine guide-override dropdowns). Per-conversation guide selection
  // was removed with the composer guide selector.
  const [guides, setGuides] = useState<Guide[]>([]);
  const [guidesLoaded, setGuidesLoaded] = useState(false);
  // Provider lock map. Older builds' home composer wrote locks for the
  // in-memory home-draft key on send, permanently poisoning this persisted
  // map (nothing ever removes an entry) and filtering the home model dropdown
  // to a single provider. Strip any legacy HOME_DRAFT_KEY entry on init; the
  // localStorage blob itself is left stale on purpose — the next lock write
  // re-serializes the cleaned in-memory map.
  const [lockedProviders, setLockedProviders] = useState<Record<string, string>>(() => {
    // One-time migration from the pre-rename (praixy) key so existing
    // conversations keep their provider lock.
    const legacy = localStorage.getItem('praixy_locked_providers');
    if (legacy !== null && localStorage.getItem('quest_locked_providers') === null) {
      localStorage.setItem('quest_locked_providers', legacy);
    }
    if (legacy !== null) {
      localStorage.removeItem('praixy_locked_providers');
    }
    const stored = localStorage.getItem('quest_locked_providers');
    const parsed: Record<string, string> = stored ? JSON.parse(stored) : {};
    delete parsed[HOME_DRAFT_KEY];
    return parsed;
  });

  // Project state
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [drilledProjectId, setDrilledProjectId] = useState<string | null>(null);

  // Pending routine message (set by Sidebar when a routine is invoked, consumed by ChatPanel)
  const [pendingRoutineMessage, setPendingRoutineMessage] = useState<{
    conversationId: string;
    prompt: string;
    guideId: string | null;
  } | null>(null);

  // Pending first message (set by the root HomeComposer, consumed by ChatPanel)
  const [pendingFirstMessage, setPendingFirstMessage] = useState<{
    conversationId: string;
    prompt: string;
    model: string;
    skillIds: string[];
    flags: string[];
    isPublicProject: boolean;
    attachedFilenames: string[];
    attachments: ComposerAttachmentRef[];
  } | null>(null);

  // Requests view state
  const [showRequestsView, setShowRequestsView] = useState(false);

  // Scroll-to-message state (set by search, consumed by ChatPanel)
  const [scrollToMessageIndex, setScrollToMessageIndex] = useState<number | null>(null);

  // Per-conversation loaded skills state
  const [conversationQueuedSkills, setConversationQueuedSkills] = useState<Record<string, string[]>>({});
  const [conversationLoadedSkills, setConversationLoadedSkills] = useState<Record<string, string[]>>({});

  // Admin state (populated from /app/api/me response)
  const [isAdmin, setIsAdmin] = useState(false);

  // Server-globally enabled optional features (populated from /app/api/me)
  const [enabledFeatures, setEnabledFeatures] = useState<string[]>([]);
  // Appearance theme (see ConversationContextValue.theme)
  const [theme, setThemeState] = useState<ThemePreference>(() => readCachedTheme());
  const [colorTheme, setColorThemeState] = useState<ColorThemeId>(() => readCachedColorTheme());

  // Impersonation state (populated from /app/api/me response)
  const [isImpersonating, setIsImpersonating] = useState(false);
  const [impersonatorEmail, setImpersonatorEmail] = useState<string | null>(null);
  const [impersonatorName, setImpersonatorName] = useState<string | null>(null);

  // Version update detection
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const initialVersionHash = useRef<string | null>(null);

  // App name: "DevQuest" in local mode, "Quest" in staging/production.
  // Defaults to "Quest" until the backend config is fetched.
  const [appName, setAppName] = useState("Quest");
  const [isDevMode, setIsDevMode] = useState(false);
  const [loginRestriction, setLoginRestriction] = useState("approved accounts");
  // null until the config fetch resolves, so the sign-in screen does not
  // flash the wrong form.
  const [loginMethod, setLoginMethod] = useState<LoginMethod | null>(null);
  const [passwordSelfService, setPasswordSelfService] = useState(false);
  const [hasPassword, setHasPassword] = useState(false);
  const [availableModelIds, setAvailableModelIds] = useState<string[] | null>(null);
  // Ref mirror of availableModelIds plus the in-flight config fetch, so async
  // default-model resolution (initial /me hydration, refreshDefaultModel) can
  // await the credentialed-model list without racing the state update.
  const availableModelIdsRef = useRef<string[] | null>(null);
  const configFetchRef = useRef<Promise<void> | null>(null);

  // Fetch app config (unauthenticated) and check session on mount
  useEffect(() => {
    // Fetch quest_env from backend to determine app name
    const doFetchConfig = async () => {
      try {
        const response = await fetch('/app/api/config');
        if (response.ok) {
          const data = await response.json();
          // 'local' is the canonical mode name; 'dev' is the legacy alias
          // (still returned by older backends).
          if (data.quest_env === 'local' || data.quest_env === 'dev') {
            setAppName('DevQuest');
            setIsDevMode(true);
          }
          // The model catalog must land before available_models: consumers
          // re-render on the availableModelIds state change and read the
          // catalog (module state) during that render.
          setModelCatalog(data.models);
          if (Array.isArray(data.available_models)) {
            availableModelIdsRef.current = data.available_models;
            setAvailableModelIds(data.available_models);
          }
          if (typeof data.login_restriction === 'string' && data.login_restriction) {
            setLoginRestriction(data.login_restriction);
          } else if (typeof data.allowed_login_domain === 'string' && data.allowed_login_domain) {
            // Older backends only send the domain.
            setLoginRestriction(`@${data.allowed_login_domain} accounts`);
          }
          // Older backends have no login_method: Google sign-in.
          setLoginMethod(data.login_method === 'password' ? 'password' : 'google');
          setPasswordSelfService(data.password_self_service === true);
        } else {
          setLoginMethod('google');
        }
      } catch {
        // Config fetch failed; keep default "Quest"
        setLoginMethod('google');
      }
    };
    configFetchRef.current = doFetchConfig();

    // Fetch initial server version hash (unauthenticated)
    const doFetchVersion = async () => {
      try {
        const data = await fetchVersion();
        initialVersionHash.current = data.git_hash;
      } catch {
        // Version fetch failed; leave hash null (polling will be a no-op)
      }
    };
    doFetchVersion();

    const doCheckAuth = async () => {
      const userInfo = await checkSession();
      // Wait for the concurrent config fetch so the default-model resolution
      // below sees the credentialed-model list (it never rejects; on fetch
      // failure the list stays null and resolution keeps today's behavior).
      if (configFetchRef.current) await configFetchRef.current;
      if (userInfo) {
        setUserEmail(userInfo.email);
        setUserName(userInfo.name || '');
        setGoogleServicesConnected(userInfo.google_services_connected);
        setHasAnyServiceConnected(userInfo.has_any_service_connected);
        setIsAdmin(userInfo.is_admin);
        setEnabledFeatures(userInfo.enabled_features ?? []);
        setHasPassword(userInfo.has_password === true);
        // The server-stored theme wins over the localStorage cache (which
        // only exists to avoid a pre-paint flash on this device).
        const serverTheme = normalizeTheme(userInfo.theme);
        applyTheme(serverTheme);
        cacheTheme(serverTheme);
        setThemeState(serverTheme);
        const serverColorTheme = normalizeColorTheme(userInfo.color_theme);
        applyColorTheme(serverColorTheme);
        cacheColorTheme(serverColorTheme);
        setColorThemeState(serverColorTheme);
        setIsImpersonating(userInfo.is_impersonating);
        setImpersonatorEmail(userInfo.impersonator_email);
        setImpersonatorName(userInfo.impersonator_name);
        // Hydrate the per-visibility default models from /me (remap +
        // validate against the admin allow-list for the visibility and the
        // credentialed-model list + best-offerable fallback). Same
        // resolution used by refreshDefaultModel().
        for (const visibility of ['private', 'public'] as ModelVisibility[]) {
          setDefaultModelState(
            resolveDefaultModel(defaultModelCandidates(userInfo, visibility), availableModelIdsRef.current, visibility),
            visibility,
          );
        }
        setIsAuthenticated(true);
      } else {
        setIsAuthenticated(false);
      }
      setIsCheckingAuth(false);
    };
    doCheckAuth();
  }, []);

  // Poll for server version changes
  useEffect(() => {
    const checkVersion = async () => {
      if (document.hidden) return;
      if (initialVersionHash.current === null) return;
      try {
        const data = await fetchVersion();
        if (data.git_hash !== null && data.git_hash !== initialVersionHash.current) {
          setUpdateAvailable(true);
        }
      } catch {
        // Silently ignore network errors
      }
    };

    const intervalId = setInterval(() => {
      if (updateAvailable) return; // Stop polling once detected
      checkVersion();
    }, VERSION_POLL_INTERVAL_MS);

    const handleVisibilityChange = () => {
      if (!document.hidden && !updateAvailable) {
        checkVersion();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [updateAvailable]);

  const setDefaultModel = useCallback((model: string, visibility: ModelVisibility = 'private') => {
    // In-memory only: reflects a picker change in the current composer's
    // display. Does NOT write the server default or localStorage -- the
    // user-level default is persisted only on first-send (persistDefaultModel).
    setDefaultModelState(model, visibility);
  }, [setDefaultModelState]);

  // Re-fetch the per-user defaults from the server (GET /me) and re-apply the
  // one for the requested visibility. Invoked on every fresh new-chat composer
  // mount and on every private<->public context switch so the composer
  // reflects the latest server value across tabs without any cross-tab push.
  const refreshDefaultModel = useCallback(async (visibility: ModelVisibility = 'private'): Promise<string> => {
    const userInfo = await checkSession();
    // Make sure the mount-time config fetch has settled so resolution sees the
    // credentialed-model list (no-op after the first composer mount).
    if (configFetchRef.current) await configFetchRef.current;
    const resolved = resolveDefaultModel(
      defaultModelCandidates(userInfo, visibility),
      availableModelIdsRef.current,
      visibility,
    );
    setDefaultModelState(resolved, visibility);
    return resolved;
  }, [setDefaultModelState]);

  // Persist the user-level last-used pick for a visibility server-side
  // (best-effort, fire-and-forget). The ONLY write of either key; called
  // exclusively from the first-send paths. Does not touch localStorage.
  const persistDefaultModel = useCallback((model: string, visibility: ModelVisibility = 'private') => {
    const key = visibility === 'public' ? 'public_default_model' : 'default_model';
    fetch('/app/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: model }),
    }).catch(() => {});
  }, []);

  const getModelForConversation = useCallback((conversationId: string) => {
    const raw = conversationModels[conversationId] || defaultModel;
    return DEPRECATED_MODEL_MAP[raw] || raw;
  }, [conversationModels, defaultModel]);

  const setModelForConversation = useCallback((conversationId: string, model: string) => {
    // Update the in-memory per-conversation model. This is the per-conversation
    // override (persisted on the conversation row below); it does NOT mutate the
    // user-level default model (that is written only on a new chat's first send).
    setConversationModels((prev) => ({ ...prev, [conversationId]: model }));
    // Persist the per-conversation override to server (best-effort, fire-and-forget)
    fetch(`/app/api/conversations/${conversationId}/model`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    }).catch(() => {});
  }, []);

  const hydrateModelForConversation = useCallback((conversationId: string, model: string) => {
    // Set the in-memory per-conversation model from the server response.
    // Does NOT update defaultModel or fire a PATCH back to the server.
    setConversationModels((prev) => ({ ...prev, [conversationId]: model }));
  }, []);

  const setDraftModelForConversation = useCallback((
    conversationId: string,
    model: string,
    visibility: ModelVisibility = 'private',
  ) => {
    // Home-composer model setter: update the in-memory per-conversation map (and
    // the visibility's default for display). Do NOT PATCH the server -- the
    // draft-keyed "conversation" does not exist yet. Do NOT persist the
    // user-level default here either: selecting a model without sending must
    // not write the default. The default is persisted on first send
    // (persistDefaultModel, in ChatPanel's first-send paths). No localStorage.
    setConversationModels((prev) => ({ ...prev, [conversationId]: model }));
    setDefaultModelState(model, visibility);
  }, [setDefaultModelState]);

  const getFileBrowserState = useCallback((conversationId: string): FileBrowserState => {
    return fileBrowserStates[conversationId] || { path: '/', history: ['/'], historyIndex: 0 };
  }, [fileBrowserStates]);

  const setFileBrowserState = useCallback((conversationId: string, state: FileBrowserState) => {
    setFileBrowserStates((prev) => ({ ...prev, [conversationId]: state }));
  }, []);

  // Load guides from API
  const loadGuides = useCallback(async () => {
    try {
      const response = await fetchGuides();
      setGuides(response.guides);
      setGuidesLoaded(true);
    } catch (err) {
      console.error('Failed to load guides:', err);
    }
  }, []);

  // Load projects from API
  const loadProjects = useCallback(async () => {
    try {
      const response = await fetchProjects();
      setProjects(response.projects);
      setProjectsLoaded(true);
    } catch (err) {
      console.error('Failed to load projects:', err);
    }
  }, []);

  // Load projects once authenticated
  useEffect(() => {
    if (isAuthenticated) {
      loadProjects();
    }
  }, [isAuthenticated, loadProjects]);

  // Guides are behind the admin `guides` feature gate: GET /guides 403s
  // while it is closed for this user, so only fetch the list while the
  // gate is on (enabled_features comes from the same GET /me session
  // check) and drop any loaded list when it closes.
  const guidesEnabled = enabledFeatures.includes('guides');
  useEffect(() => {
    if (!isAuthenticated) return;
    if (guidesEnabled) {
      loadGuides();
    } else {
      setGuides([]);
      setGuidesLoaded(false);
    }
  }, [isAuthenticated, guidesEnabled, loadGuides]);

  // Open the persistent multiplexed WebSocket once authenticated; close it
  // on logout / account-delete. The connection is a single per-tab socket
  // that carries per-user globals (request count, conversation list,
  // wait-handle resolutions) and per-conversation events.
  useEffect(() => {
    if (!isAuthenticated) return;
    persistentWebSocket.connect();
    return () => {
      persistentWebSocket.disconnect();
    };
  }, [isAuthenticated]);

  const getLockedProvider = useCallback((conversationId: string): string | null => {
    return lockedProviders[conversationId] || null;
  }, [lockedProviders]);

  const lockConversationProvider = useCallback((conversationId: string, provider: string) => {
    setLockedProviders((prev) => {
      const updated = { ...prev, [conversationId]: provider };
      localStorage.setItem('quest_locked_providers', JSON.stringify(updated));
      return updated;
    });
  }, []);

  const isProviderLocked = useCallback((conversationId: string): boolean => {
    return lockedProviders[conversationId] != null;
  }, [lockedProviders]);

  const refreshConnectionStatus = useCallback(async () => {
    const userInfo = await checkSession();
    if (userInfo) {
      setGoogleServicesConnected(userInfo.google_services_connected);
      setHasAnyServiceConnected(userInfo.has_any_service_connected);
    }
  }, []);

  const refreshModelCatalog = useCallback(async () => {
    try {
      const response = await fetch('/app/api/config');
      if (!response.ok) return;
      const data = await response.json();
      // Catalog before available_models: consumers re-render on the state
      // change and read the (module-state) catalog during that render.
      setModelCatalog(data.models);
      if (Array.isArray(data.available_models)) {
        availableModelIdsRef.current = data.available_models;
        setAvailableModelIds(data.available_models);
      }
    } catch {
      // Best-effort: the next page load picks the change up anyway.
    }
  }, []);

  const refreshEnabledFeatures = useCallback(async () => {
    const userInfo = await checkSession();
    if (userInfo) {
      setEnabledFeatures(userInfo.enabled_features ?? []);
    }
  }, []);

  // Apply + cache the theme right away (no flash while the request is in
  // flight), then persist it server-side. Rejects on a failed PUT so the
  // Appearance section can surface the error; the local choice stays applied.
  const setTheme = useCallback(async (next: ThemePreference) => {
    applyTheme(next);
    cacheTheme(next);
    setThemeState(next);
    const response = await fetch('/app/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: next }),
    });
    if (!response.ok) {
      throw new Error(`Failed to save theme (HTTP ${response.status})`);
    }
  }, []);

  const setColorTheme = useCallback(async (next: ColorThemeId) => {
    applyColorTheme(next);
    cacheColorTheme(next);
    setColorThemeState(next);
    const response = await fetch('/app/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ color_theme: next }),
    });
    if (!response.ok) {
      throw new Error(`Failed to save colour theme (HTTP ${response.status})`);
    }
  }, []);

  // Per-conversation loaded skills helpers
  const getQueuedSkillsForConversation = useCallback((conversationId: string): string[] => {
    return conversationQueuedSkills[conversationId] || [];
  }, [conversationQueuedSkills]);

  const setQueuedSkillsForConversationCb = useCallback((conversationId: string, skillIds: string[]) => {
    setConversationQueuedSkills((prev) => ({ ...prev, [conversationId]: skillIds }));
  }, []);

  const getLoadedSkillsForConversation = useCallback((conversationId: string): string[] => {
    return conversationLoadedSkills[conversationId] || [];
  }, [conversationLoadedSkills]);

  const setLoadedSkillsForConversationCb = useCallback((conversationId: string, skillIds: string[]) => {
    setConversationLoadedSkills((prev) => ({ ...prev, [conversationId]: skillIds }));
  }, []);

  const markSkillsAsLoaded = useCallback((conversationId: string, skillIds: string[]) => {
    // Merge the given IDs into the loaded set
    setConversationLoadedSkills((prev) => {
      const existing = prev[conversationId] || [];
      const existingSet = new Set(existing);
      const merged = [...existing, ...skillIds.filter((id) => !existingSet.has(id))];
      return { ...prev, [conversationId]: merged };
    });
    // Clear the queued set for this conversation
    setConversationQueuedSkills((prev) => {
      const next = { ...prev };
      delete next[conversationId];
      return next;
    });
  }, []);

  // Memoize the context value to prevent unnecessary re-renders of all consumers
  // when unrelated state in the provider changes. Only creates a new value reference
  // when one of the constituent values actually changes.
  const value: ConversationContextValue = useMemo(() => ({
    activeConversationId,
    setActiveConversationId,
    isAuthenticated,
    isCheckingAuth,
    appName,
    isDevMode,
    loginRestriction,
    loginMethod,
    passwordSelfService,
    hasPassword,
    setHasPassword,
    availableModelIds,
    defaultModel,
    defaultModels,
    setDefaultModel,
    refreshDefaultModel,
    persistDefaultModel,
    getModelForConversation,
    setModelForConversation,
    hydrateModelForConversation,
    setDraftModelForConversation,
    getFileBrowserState,
    setFileBrowserState,
    userEmail,
    userName,
    isSettingsOpen,
    setSettingsOpen,
    settingsInitialSection,
    setSettingsInitialSection,
    googleServicesConnected,
    hasAnyServiceConnected,
    refreshConnectionStatus,
    guides,
    guidesLoaded,
    loadGuides,
    getLockedProvider,
    lockConversationProvider,
    isProviderLocked,
    projects,
    projectsLoaded,
    loadProjects,
    activeProjectId,
    setActiveProjectId,
    drilledProjectId,
    setDrilledProjectId,
    pendingRoutineMessage,
    setPendingRoutineMessage,
    pendingFirstMessage,
    setPendingFirstMessage,
    showRequestsView,
    setShowRequestsView,
    scrollToMessageIndex,
    setScrollToMessageIndex,
    getQueuedSkillsForConversation,
    setQueuedSkillsForConversation: setQueuedSkillsForConversationCb,
    getLoadedSkillsForConversation,
    setLoadedSkillsForConversation: setLoadedSkillsForConversationCb,
    markSkillsAsLoaded,
    refreshModelCatalog,
    isAdmin,
    enabledFeatures,
    refreshEnabledFeatures,
    theme,
    setTheme,
    colorTheme,
    setColorTheme,
    isImpersonating,
    impersonatorEmail,
    impersonatorName,
    updateAvailable,
  }), [
    activeConversationId,
    setActiveConversationId,
    isAuthenticated,
    isCheckingAuth,
    appName,
    isDevMode,
    loginRestriction,
    loginMethod,
    passwordSelfService,
    hasPassword,
    setHasPassword,
    availableModelIds,
    defaultModel,
    defaultModels,
    setDefaultModel,
    refreshDefaultModel,
    persistDefaultModel,
    getModelForConversation,
    setModelForConversation,
    hydrateModelForConversation,
    setDraftModelForConversation,
    getFileBrowserState,
    setFileBrowserState,
    userEmail,
    userName,
    isSettingsOpen,
    setSettingsOpen,
    settingsInitialSection,
    setSettingsInitialSection,
    googleServicesConnected,
    hasAnyServiceConnected,
    refreshConnectionStatus,
    guides,
    guidesLoaded,
    loadGuides,
    getLockedProvider,
    lockConversationProvider,
    isProviderLocked,
    projects,
    projectsLoaded,
    loadProjects,
    activeProjectId,
    setActiveProjectId,
    drilledProjectId,
    setDrilledProjectId,
    pendingRoutineMessage,
    setPendingRoutineMessage,
    pendingFirstMessage,
    setPendingFirstMessage,
    showRequestsView,
    setShowRequestsView,
    scrollToMessageIndex,
    setScrollToMessageIndex,
    getQueuedSkillsForConversation,
    setQueuedSkillsForConversationCb,
    getLoadedSkillsForConversation,
    setLoadedSkillsForConversationCb,
    markSkillsAsLoaded,
    refreshModelCatalog,
    isAdmin,
    enabledFeatures,
    refreshEnabledFeatures,
    theme,
    setTheme,
    colorTheme,
    setColorTheme,
    isImpersonating,
    impersonatorEmail,
    impersonatorName,
    updateAvailable,
  ]);

  return <ConversationContext.Provider value={value}>{children}</ConversationContext.Provider>;
}

/**
 * Hook to access conversation context
 */
export function useConversationContext(): ConversationContextValue {
  const context = useContext(ConversationContext);
  if (!context) {
    throw new Error('useConversationContext must be used within a ConversationProvider');
  }
  return context;
}
