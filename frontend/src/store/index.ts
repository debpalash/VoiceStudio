/**
 * Zustand store root — Phase 2.2 (ROADMAP.md).
 *
 * Goal: peel state off the 1,803-line App.jsx monolith a slice at a time,
 * without big-bang disruption. Every slice lives in its own file, and the
 * root store composes them.
 *
 * Rule of thumb:
 *   - UI primitives own their local state (don't lift it).
 *   - App-level state (active project, user prefs, pipeline progress) lives
 *     here.
 *   - Selectors live at call sites (`useStore(s => s.foo)`).
 *
 * Zustand persistence keeps bounded preferences in localStorage and stores
 * unbounded long-form documents in IndexedDB.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import { createLongformZustandStorage } from '../utils/longformPersistence';

import type { PrefsSlice } from './prefsSlice';
import { createPrefsSlice, FONT_OPTIONS, FONT_STACKS } from './prefsSlice';

// Re-export font preference tables so panels can import from the store root.
export { FONT_OPTIONS, FONT_STACKS };
import type { GlossarySlice } from './glossarySlice';
import { createGlossarySlice } from './glossarySlice';
import type { UiSlice } from './uiSlice';
import { createUiSlice } from './uiSlice';
import type { DubSlice } from './dubSlice';
import { createDubSlice } from './dubSlice';
import type { GenerateSlice } from './generateSlice';
import { createGenerateSlice } from './generateSlice';
import type { PillSlice } from './pillSlice';
import { createPillSlice } from './pillSlice';
import type { LongformOverrides, LongformSlice } from './longformSlice';
import {
  createLongformSlice,
  DEFAULT_OVERRIDES,
  genProjectId,
  SLICE_DEFAULTS,
} from './longformSlice';
import type { UpdaterSlice } from './updaterSlice';
import { createUpdaterSlice } from './updaterSlice';
import type { GallerySlice } from './gallerySlice';
import { createGallerySlice } from './gallerySlice';
import type { ReleasesSlice } from './releasesSlice';
import { createReleasesSlice } from './releasesSlice';
import type { DonationSlice } from './donationSlice';
import { createDonationSlice } from './donationSlice';

export type AppStore = PrefsSlice &
  GlossarySlice &
  UiSlice &
  DubSlice &
  GenerateSlice &
  PillSlice &
  LongformSlice &
  UpdaterSlice &
  GallerySlice &
  ReleasesSlice &
  DonationSlice;

export const APP_STORE_KEY = 'omnivoice.app';

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function nullableFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function normalizeLongformOverrides(value: unknown): LongformOverrides {
  if (!isRecord(value)) return { ...DEFAULT_OVERRIDES };
  return {
    numStep: nullableFiniteNumber(value.numStep),
    guidanceScale: nullableFiniteNumber(value.guidanceScale),
    posTemp: nullableFiniteNumber(value.posTemp),
    classTemp: nullableFiniteNumber(value.classTemp),
    postprocess:
      typeof value.postprocess === 'boolean' ? value.postprocess : DEFAULT_OVERRIDES.postprocess,
    seed: nullableFiniteNumber(value.seed),
    varyRepeats:
      typeof value.varyRepeats === 'boolean' ? value.varyRepeats : DEFAULT_OVERRIDES.varyRepeats,
    emoText: typeof value.emoText === 'string' ? value.emoText : DEFAULT_OVERRIDES.emoText,
    emoAlpha: nullableFiniteNumber(value.emoAlpha),
    lineGapMs: nullableFiniteNumber(value.lineGapMs),
    paragraphGapMs: nullableFiniteNumber(value.paragraphGapMs),
    trimEdges: typeof value.trimEdges === 'boolean' ? value.trimEdges : DEFAULT_OVERRIDES.trimEdges,
  };
}

/** Shape-safe, non-throwing upgrade for the legacy v4 project records. */
export function migrateAppStore(persisted: unknown, version: number): Partial<AppStore> {
  if (!isRecord(persisted)) return {} as Partial<AppStore>;
  const p = persisted;
  if (version < 4 && !Object.prototype.hasOwnProperty.call(p, 'timingStrategy')) {
    p.timingStrategy = 'concise';
  }
  if (version < 5) {
    const raw = Array.isArray(p.storyProjects) ? p.storyProjects : [];
    p.storyProjects = raw.filter(isRecord).map((sp) => ({
      ...sp,
      id: typeof sp.id === 'string' && sp.id.trim() ? sp.id : genProjectId(),
      name: typeof sp.name === 'string' && sp.name.trim() ? sp.name : 'Untitled',
      mode: sp.mode === 'audiobook' ? 'audiobook' : 'stories',
      cast: Array.isArray(sp.cast) ? sp.cast : [],
      tracks: Array.isArray(sp.tracks) ? sp.tracks : [],
      script: typeof sp.script === 'string' ? sp.script : SLICE_DEFAULTS.script,
      meta: isRecord(sp.meta) ? sp.meta : {},
      lexicon: isRecord(sp.lexicon) ? sp.lexicon : {},
      coverRef: isRecord(sp.coverRef) ? sp.coverRef : null,
      outputFormat: sp.outputFormat === 'mp3' ? 'mp3' : 'm4b',
      loudness: sp.loudness === 'acx' || sp.loudness === 'podcast' ? sp.loudness : 'off',
      defaultVoice: typeof sp.defaultVoice === 'string' ? sp.defaultVoice : null,
      language: typeof sp.language === 'string' ? sp.language : SLICE_DEFAULTS.language,
      overrides: normalizeLongformOverrides(sp.overrides),
      voiceCast: isRecord(sp.voiceCast) ? sp.voiceCast : {},
      updatedAt:
        typeof sp.updatedAt === 'number' && Number.isFinite(sp.updatedAt) ? sp.updatedAt : 0,
    }));
    p.projectMode = 'stories';
  }
  if (version < 7) p.uiScaleConfigured = true;
  return p as Partial<AppStore>;
}

/**
 * `useAppStore` — single root store. Don't create siblings. Slices compose here.
 *
 * Usage:
 *   const quality = useAppStore(s => s.translateQuality);
 *   const setQuality = useAppStore(s => s.setTranslateQuality);
 */
export const useAppStore = create<AppStore>()(
  persist(
    (set, get, api) => ({
      ...createPrefsSlice(set, get, api),
      ...createGlossarySlice(set, get, api),
      ...createUiSlice(set, get, api),
      ...createDubSlice(set, get, api),
      ...createGenerateSlice(set, get, api),
      ...createPillSlice(set, get, api),
      ...createLongformSlice(set, get, api),
      ...createUpdaterSlice(set, get, api), // transient — not in partialize
      ...createGallerySlice(set, get, api),
      ...createReleasesSlice(set, get, api), // transient — not in partialize
      ...createDonationSlice(set, get, api),
    }),
    {
      name: APP_STORE_KEY,
      storage: createLongformZustandStorage(),
      // Only persist user prefs + glossary. Pipeline / transient state is opt-out.
      partialize: (s) => ({
        translateQuality: s.translateQuality,
        autoGlossary: s.autoGlossary,
        reflectPass: s.reflectPass,
        condenseSuggest: s.condenseSuggest,
        dualSubs: s.dualSubs,
        burnSubs: s.burnSubs,
        karaokeSubs: s.karaokeSubs,
        glossaryVisible: s.glossaryVisible,
        reviewMode: s.reviewMode,
        showHeaderLiveStats: s.showHeaderLiveStats,
        timingStrategy: s.timingStrategy,
        fitOptions: s.fitOptions,
        voiceMatch: s.voiceMatch,
        dubLivePreview: s.dubLivePreview,
        // "What's new" affordance (feat/safe-updates) — remembering which
        // version's notes were seen only works if it survives restarts.
        whatsNewSeenVersion: s.whatsNewSeenVersion,
        // Dismissed system-notification ids — a dismissal only means anything
        // if it survives restarts (the notes are re-emitted on every poll).
        dismissedNotificationIds: s.dismissedNotificationIds,
        autoPlayPreview: s.autoPlayPreview,
        mode: s.mode,
        defineMethod: s.defineMethod,
        isSidebarCollapsed: s.isSidebarCollapsed,
        isSidebarProjectsCollapsed: s.isSidebarProjectsCollapsed,
        sidebarTab: s.sidebarTab,
        uiScale: s.uiScale,
        uiScaleConfigured: s.uiScaleConfigured,
        // Rail vs titlebar tabs — a chrome preference, so it sticks like scale.
        navStyle: s.navStyle,
        locale: s.locale,
        // Explicit-choice + first-run-offer flags must survive restarts, or the
        // one-time "Switch to English?" offer would re-nag on every launch.
        localeChosen: s.localeChosen,
        langPromptSeen: s.langPromptSeen,
        theme: s.theme,
        font: s.font,
        // Generate-tab prefs — users expect their synthesis knobs to stick.
        language: s.language,
        speed: s.speed,
        steps: s.steps,
        cfg: s.cfg,
        tShift: s.tShift,
        posTemp: s.posTemp,
        classTemp: s.classTemp,
        layerPenalty: s.layerPenalty,
        denoise: s.denoise,
        postprocess: s.postprocess,
        vdStates: s.vdStates,
        // Voice gallery — favorites + view/zone/filter preferences stick.
        favoriteArchetypeIds: s.favoriteArchetypeIds,
        galleryViewMode: s.galleryViewMode,
        galleryZone: s.galleryZone,
        archetypeFilters: s.archetypeFilters,
        // The split storage adapter moves long-form payloads to IndexedDB and
        // strips transient runtime fields there, at the deferred commit point.
        storyTracks: s.storyTracks,
        cast: s.cast,
        storyProjects: s.storyProjects,
        currentProjectId: s.currentProjectId,
        // Long-form shared working fields (#31) — persist so Audiobook
        // metadata/script/prefs survive a tab switch or reload once bound (#31b).
        script: s.script,
        meta: s.meta,
        lexicon: s.lexicon,
        voiceCast: s.voiceCast,
        coverRef: s.coverRef,
        outputFormat: s.outputFormat,
        loudness: s.loudness,
        defaultVoice: s.defaultVoice,
        // Server filename of the last finished longform render (#1139) — a
        // plain /audio path (never a blob: URL), so rehydrating it is safe
        // and keeps the finished book's Download affordance reachable.
        lastOutput: s.lastOutput,
        lastOutputScript: s.lastOutputScript,
        lastOutputChapters: s.lastOutputChapters,
        projectMode: s.projectMode,
        // Donation prompt state (#007) — persist everything EXCEPT
        // `shownThisSession` so the ≤1/session cap resets on every launch.
        successCount: s.successCount,
        dubCount: s.dubCount,
        firstSuccessAt: s.firstSuccessAt,
        lastShownAt: s.lastShownAt,
        shownCount: s.shownCount,
        firedMilestones: s.firedMilestones,
        optedOut: s.optedOut,
      }),
      version: 9,
      // IndexedDB hydration is asynchronous. Bootstrap resolves main/widget
      // ownership first, then explicitly hydrates before React renders.
      skipHydration: true,
      // Drop old persisted shapes rather than crashing the app. Every field
      // has a safe default in its slice, so v1/v2/v3 users pick up v4 defaults
      // for new fields (timingStrategy etc.) and keep any keys we still write
      // today. Upgrade > crash.
      // v9 moves unbounded long-form payloads to IndexedDB. The storage adapter
      // commits that durable record before this migration can trim localStorage.
      migrate: migrateAppStore,
    },
  ),
);
