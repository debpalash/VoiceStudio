/**
 * UI / navigation slice — Phase 2.2 (App.jsx monolith reduction).
 *
 * Holds the always-on "where am I in the app?" state that used to live as a
 * fan of `useState` calls at the top of App.jsx. Moving this out makes the
 * top of App.jsx readable again and lets deep children (Sidebar, NavRail,
 * VoiceProfile) read current mode / active project without prop-drilling
 * through the whole tree.
 *
 * Persisted: mode (the tab you were on), isSidebarCollapsed, uiScale. The
 * active-project / active-voice ids are transient — on reload we snap back
 * to the launchpad rather than half-load a stale project state.
 */
import type { StateCreator } from 'zustand';
import type { EngineFamily } from '../api/types';

export type AppMode =
  | 'launchpad'
  | 'generate'
  | 'dub'
  | 'studio'
  // Legacy navigation ids — consolidated into 'studio' (voice-studio-unification
  // P4). Kept in the union so persisted UI state / history items that still say
  // 'clone'/'design' type-check while the restore shims map them to 'studio'.
  | 'clone'
  | 'design'
  | 'stories'
  | 'voice'
  | 'tools'
  | 'batch'
  | 'contact'
  | 'catalogue'
  | 'settings';

/** Which pane the Model Catalogue workspace opens on. */
/**
 * Deep-link target for the Model Catalogue: the engine family to open on. The
 * page has one axis now (TTS / ASR / LLM) — the old `pane` key is tolerated
 * and ignored so stale callers keep navigating.
 */
export type CatalogueTarget = EngineFamily | { family?: EngineFamily | null; pane?: string };

/**
 * The Voice workspace's "Define voice" method (was the Clone/Design tab
 * split): 'audio' = define from reference audio (old Clone tab), 'design' =
 * define by described attributes (old Design tab), 'convert' = speech-to-
 * speech voice changer (re-say a source clip in an existing profile's voice).
 */
type DefineMethod = 'audio' | 'design' | 'convert';

type SidebarTab = 'projects' | 'history' | 'downloads';

/**
 * Which navigation skin renders the workspace switcher:
 *  - 'rail': the vertical icon rail down the window edge (default)
 *  - 'tabs': browser-style tabs in the title bar
 * Both render `components/navItems.js`; see `TitleTabs.jsx`.
 */
export type NavStyle = 'rail' | 'tabs';

export interface UiSlice {
  mode: AppMode;
  /** Active definition method inside the Voice ('studio') workspace. */
  defineMethod: DefineMethod;
  activeProjectId: string | null;
  activeProjectName: string;
  activeVoiceId: string | null;
  /** The mode the user was on before opening a voice profile. "Back" restores it. */
  modeBeforeVoice: AppMode | null;
  /**
   * One-shot hand-off for "use this voice in the synthesis view": the Gallery
   * (or any view) sets a profile id and navigates to `studio`; App.jsx selects
   * that profile once it appears in the loaded profiles list, then clears this.
   */
  pendingProfileId: string | null;
  /**
   * One-shot hand-off for "open Settings on a specific tab": a caller (e.g. the
   * footer version badge) sets the tab id and navigates to `settings`; the
   * Settings page consumes it as its initial/active tab, then clears it. Mirrors
   * `pendingProfileId`.
   */
  pendingSettingsTab: string | null;
  /**
   * One-shot hand-off for "open the Model Catalogue on a specific engine
   * family" — the catalogue twin of `pendingSettingsTab`.
   */
  pendingCatalogueFamily: EngineFamily | null;
  isSidebarCollapsed: boolean;
  isSidebarProjectsCollapsed: boolean;
  sidebarTab: SidebarTab;
  showCheatsheet: boolean;
  uiScale: number;
  /** True after the first-start scale check has been confirmed. */
  uiScaleConfigured: boolean;
  /** Transient: an unconfirmed first-start scale is being previewed. */
  uiScalePreviewed: boolean;
  navStyle: NavStyle;

  setMode: (mode: AppMode) => void;
  setDefineMethod: (method: DefineMethod) => void;
  setActiveProject: (id: string | null, name?: string) => void;
  setActiveVoiceId: (id: string | null) => void;
  setModeBeforeVoice: (mode: AppMode | null) => void;
  setPendingProfileId: (id: string | null) => void;
  setPendingSettingsTab: (tab: string | null) => void;
  setPendingCatalogueFamily: (family: EngineFamily | null) => void;
  /** Navigate to Settings on a specific tab in one call. */
  openSettingsTab: (tab: string) => void;
  /** Navigate to the Model Catalogue on a specific engine family in one call. */
  openCatalogue: (target?: CatalogueTarget | null) => void;
  setIsSidebarCollapsed: (collapsed: boolean) => void;
  setIsSidebarProjectsCollapsed: (collapsed: boolean) => void;
  setSidebarTab: (tab: SidebarTab) => void;
  setShowCheatsheet: (open: boolean | ((prev: boolean) => boolean)) => void;
  setUiScale: (scale: number) => void;
  setUiScaleConfigured: (configured: boolean) => void;
  setUiScalePreviewed: (previewed: boolean) => void;
  setNavStyle: (style: NavStyle) => void;

  /** Jump to the voice-profile page, remembering what mode you were on. */
  openVoiceProfile: (id: string) => void;
  /** Close the voice-profile page, restoring the previous mode. */
  closeVoiceProfile: () => void;
}

export const createUiSlice: StateCreator<UiSlice, [], [], UiSlice> = (set, get) => ({
  mode: 'launchpad',
  defineMethod: 'audio',
  activeProjectId: null,
  activeProjectName: '',
  activeVoiceId: null,
  modeBeforeVoice: null,
  pendingProfileId: null,
  pendingSettingsTab: null,
  pendingCatalogueFamily: null,
  isSidebarCollapsed: false,
  isSidebarProjectsCollapsed: false,
  sidebarTab: 'projects',
  showCheatsheet: false,
  // 100% by default — the app renders at native size out of the box; users
  // who prefer larger UI pick their scale in Settings → Appearance (persisted).
  uiScale: 1.0,
  uiScaleConfigured: false,
  uiScalePreviewed: false,
  // The icon rail is the out-of-the-box navigation; titlebar tabs are opt-in
  // from Settings → Appearance and persist like the other chrome preferences.
  navStyle: 'rail',

  setMode: (mode) => set({ mode }),
  setDefineMethod: (method) => set({ defineMethod: method }),
  setActiveProject: (id, name = '') => set({ activeProjectId: id, activeProjectName: name }),
  setActiveVoiceId: (id) => set({ activeVoiceId: id }),
  setModeBeforeVoice: (mode) => set({ modeBeforeVoice: mode }),
  setPendingProfileId: (id) => set({ pendingProfileId: id }),
  setPendingSettingsTab: (tab) => set({ pendingSettingsTab: tab }),
  setPendingCatalogueFamily: (family) => set({ pendingCatalogueFamily: family }),
  openSettingsTab: (tab) => set({ pendingSettingsTab: tab, mode: 'settings' }),
  openCatalogue: (target = null) => {
    const family = typeof target === 'string' ? target : (target?.family ?? null);
    set({ pendingCatalogueFamily: family, mode: 'catalogue' });
  },
  setIsSidebarCollapsed: (collapsed) => set({ isSidebarCollapsed: collapsed }),
  setIsSidebarProjectsCollapsed: (collapsed) => set({ isSidebarProjectsCollapsed: collapsed }),
  setSidebarTab: (tab) => set({ sidebarTab: tab }),
  setShowCheatsheet: (open) =>
    set((s) => ({
      showCheatsheet:
        typeof open === 'function' ? (open as (p: boolean) => boolean)(s.showCheatsheet) : open,
    })),
  setUiScale: (scale) => set({ uiScale: scale }),
  setUiScaleConfigured: (configured) => set({ uiScaleConfigured: configured }),
  setUiScalePreviewed: (previewed) => set({ uiScalePreviewed: previewed }),
  setNavStyle: (style) => set({ navStyle: style }),

  openVoiceProfile: (id) => {
    const prev = get().mode;
    set({
      mode: 'voice',
      activeVoiceId: id,
      modeBeforeVoice: prev !== 'voice' ? prev : get().modeBeforeVoice,
    });
  },
  closeVoiceProfile: () => {
    const prev = get().modeBeforeVoice;
    set({
      mode: prev ?? 'launchpad',
      activeVoiceId: null,
      modeBeforeVoice: null,
    });
  },
});
