import type { StateCreator } from 'zustand';

/**
 * Long-form project state (#31) — ONE project concept both long-form editors
 * bind to: Stories (multi-voice cast + tracks) and Audiobook (raw script + book
 * metadata). A `mode` discriminator says which content model is authoritative.
 *
 * This generalizes the former `storiesSlice`: the persisted field names
 * (`storyProjects`/`storyTracks`/`cast`/`currentProjectId`) are KEPT so every
 * existing consumer and every legacy persisted envelope keeps working with no
 * change — the project SHAPE gains optional book-identity fields, default-filled
 * on load so older records never surface `undefined` to a controlled input.
 *
 * Audiobook is NOT yet bound to this store (its inputs still use local state) —
 * that binding is the follow-up slice (#31b). This slice ships the data model +
 * migration + the `convertMode` seam #24 will consume.
 *
 * Deprecated alias (`createStoriesSlice`) is re-exported so the slice rename
 * breaks no import.
 */
interface StoryTrack {
  id: number;
  character: string; // CastMember.id
  text: string;
  profileId: string | null; // per-line voice override (else inherits cast)
  emotion: string | null; // per-line tone/instruct (Phase 3)
  speed: number | null; // per-line speed override (Phase 3)
}

export interface CastMember {
  id: string;
  name: string;
  color: string;
  profileId: string | null; // the voice this character speaks in
}

export type LongformMode = 'stories' | 'audiobook';

/** Book identity — mirrors api/audiobook.ts AudiobookMetadata (6 optional
 *  strings). Values are free-text user DATA in any language (incl. CJK book
 *  titles): stored byte-for-byte, outside the no-hardcoded-CJK source rule. */
export interface LongformMeta {
  title?: string;
  author?: string;
  narrator?: string;
  year?: string;
  genre?: string;
  description?: string;
}

/** Expressive/quality overrides for a longform render (#1208). Every field is
 *  null/''/false by default — an untouched panel reproduces today's exact
 *  render on the backend (num_step 32, guidance 2.0, model-default temps,
 *  postprocess on, no emotion, content-addressed caching). Only non-null values
 *  are sent, so the request stays backward-compatible. `emoText`/`emoAlpha`
 *  reach IndexTTS2 only; `varyRepeats` is the cache opt-out. */
export interface LongformOverrides {
  numStep: number | null;
  guidanceScale: number | null;
  posTemp: number | null;
  classTemp: number | null;
  postprocess: boolean | null;
  seed: number | null;
  varyRepeats: boolean;
  emoText: string;
  emoAlpha: number | null;
  lineGapMs: number | null;
  paragraphGapMs: number | null;
  trimEdges: boolean | null;
}

export const DEFAULT_OVERRIDES: LongformOverrides = {
  numStep: null,
  guidanceScale: null,
  posTemp: null,
  classTemp: null,
  postprocess: null,
  seed: null,
  varyRepeats: false,
  emoText: '',
  emoAlpha: null,
  lineGapMs: null,
  paragraphGapMs: null,
  trimEdges: null,
};

/** A re-uploadable cover reference. localStorage can't hold the File/blob, so
 *  we persist the picked filename + the server path from POST /audiobook/cover.
 *  serverPath is best-effort (the server may GC the temp cover); a re-picked
 *  cover always wins. serverPath is a LOCAL backend path — no cloud. */
export interface CoverRef {
  filename: string | null;
  serverPath: string | null;
}

export interface AudiobookRenderChapter {
  title: string;
  status: string;
  duration_s?: number;
}

interface LongformProject {
  id: string;
  name: string;
  mode: LongformMode;
  // Stories content (multi-voice):
  cast: CastMember[];
  tracks: StoryTrack[];
  // Audiobook content (raw script):
  script: string;
  // Shared book identity + output prefs:
  meta: LongformMeta;
  lexicon: Record<string, string>;
  coverRef: CoverRef | null;
  outputFormat: 'm4b' | 'mp3';
  loudness: 'off' | 'acx' | 'podcast';
  defaultVoice: string | null;
  language: string;
  overrides: LongformOverrides;
  // Audiobook multi-voice cast (#1217): [voice:NAME] → profile id. Empty for a
  // single-voice book; an absent field on an old record default-fills to {}.
  voiceCast: Record<string, string>;
  updatedAt: number;
}

export interface LongformSlice {
  /** Transient startup gate; never included in the persisted partial state. */
  longformPersistenceError: boolean;
  // Stories working state (existing field names — consumers unchanged):
  storyTracks: StoryTrack[];
  cast: CastMember[];
  storyProjects: LongformProject[];
  currentProjectId: string | null;
  // Shared working state (new — Audiobook will bind to these in #31b):
  script: string;
  meta: LongformMeta;
  lexicon: Record<string, string>;
  coverRef: CoverRef | null;
  outputFormat: 'm4b' | 'mp3';
  loudness: 'off' | 'acx' | 'podcast';
  defaultVoice: string | null;
  // Longform render language (#1208 / #505): 'Auto' → the profile's language,
  // else an explicit pick that reaches the backend (fixes the AudiobookGenerate
  // body that used to omit `language` entirely).
  language: string;
  // Expressive/quality overrides + cache opt-out (#1208). Persisted like the
  // lexicon so a book's tuning survives a tab switch / reload.
  overrides: LongformOverrides;
  // Audiobook cast map (#1217): [voice:NAME] → profile id. Persisted like the
  // lexicon so a book's voice assignments survive a tab switch / reload.
  voiceCast: Record<string, string>;
  // Last finished render's server filename (#1139): the Audiobook tab's
  // player + Download link used to live in component useState, so a finished
  // book's export affordance evaporated on the first tab switch and users
  // reported "no way to download". A server filename (never a blob: URL) is
  // safe to persist and rehydrate.
  lastOutput: string;
  // Render-time inputs for the persisted output. The editable manuscript may
  // change afterwards; lyrics must continue to follow the audio that exists.
  lastOutputScript: string;
  lastOutputChapters: AudiobookRenderChapter[];
  // NB: named `projectMode` (not `mode`) to avoid colliding with the app-level
  // navigation `mode` (uiSlice, AppMode). The stored LongformProject.mode is a
  // nested record field and keeps its name.
  projectMode: LongformMode;
  // Stories actions (existing):
  setStoryTracks: (tracks: StoryTrack[]) => void;
  setCast: (cast: CastMember[]) => void;
  upsertCastMember: (member: CastMember) => void;
  removeCastMember: (id: string) => void;
  setCharacterVoice: (castId: string, profileId: string | null) => void;
  // Shared-field actions (new):
  setScript: (script: string) => void;
  setProjectMeta: (patch: Partial<LongformMeta>) => void; // merge (I1)
  setLexicon: (lexicon: Record<string, string>) => void; // replace (I3)
  setVoiceCast: (name: string, profileId: string | null) => void; // merge/remove
  setOutputPrefs: (patch: {
    outputFormat?: 'm4b' | 'mp3';
    loudness?: 'off' | 'acx' | 'podcast';
    defaultVoice?: string | null;
    language?: string;
  }) => void; // merge (I2)
  setLongformOverrides: (patch: Partial<LongformOverrides>) => void; // merge
  setCoverRef: (ref: CoverRef | null) => void;
  setLastOutput: (output: string) => void;
  setLastOutputSnapshot: (
    output: string,
    script: string,
    chapters: AudiobookRenderChapter[],
  ) => void;
  convertMode: (mode: LongformMode) => void; // flips mode only (#24 seam)
  // Project lifecycle:
  saveProject: (name: string) => void;
  loadProject: (id: string) => void; // restores FULL working surface, default-filled
  newProject: (mode?: LongformMode) => void; // clears FULL surface; mode defaults 'stories'
  deleteProject: (id: string) => void;
  renameProject: (id: string, name: string) => void;
}

export const DEFAULT_CAST: CastMember[] = [
  { id: 'narrator', name: 'Narrator', color: '#fabd2f', profileId: null },
];

/** The non-content working defaults — single source of truth shared by slice
 *  init, newProject, the loadProject default-fill, and the migrate fn. */
export const SLICE_DEFAULTS = {
  script: '' as string,
  meta: {} as LongformMeta,
  lexicon: {} as Record<string, string>,
  coverRef: null as CoverRef | null,
  outputFormat: 'm4b' as 'm4b' | 'mp3',
  loudness: 'off' as 'off' | 'acx' | 'podcast',
  defaultVoice: null as string | null,
  language: 'Auto' as string,
  overrides: DEFAULT_OVERRIDES as LongformOverrides,
  voiceCast: {} as Record<string, string>,
  lastOutput: '' as string,
  lastOutputScript: '' as string,
  lastOutputChapters: [] as AudiobookRenderChapter[],
  projectMode: 'stories' as LongformMode,
} as const;

export function genProjectId(): string {
  return `p_${Math.random().toString(36).slice(2, 10)}`;
}

// Strip transient runtime fields before snapshotting into a saved project.
function snapshotTracks(tracks: StoryTrack[]): StoryTrack[] {
  return tracks.map(({ id, character, text, profileId, emotion, speed }) => ({
    id,
    character,
    text,
    profileId,
    emotion,
    speed,
  }));
}

export const createLongformSlice: StateCreator<LongformSlice, [], [], LongformSlice> = (
  set,
  get,
) => ({
  longformPersistenceError: false,
  storyTracks: [],
  cast: DEFAULT_CAST.map((c) => ({ ...c })),
  storyProjects: [],
  currentProjectId: null,
  ...SLICE_DEFAULTS,
  meta: { ...SLICE_DEFAULTS.meta },
  lexicon: { ...SLICE_DEFAULTS.lexicon },
  overrides: { ...SLICE_DEFAULTS.overrides },
  voiceCast: { ...SLICE_DEFAULTS.voiceCast },

  setStoryTracks: (storyTracks) => set({ storyTracks }),
  setCast: (cast) => set({ cast }),
  upsertCastMember: (member) =>
    set((s) => {
      const i = s.cast.findIndex((c) => c.id === member.id);
      if (i === -1) return { cast: [...s.cast, member] };
      const next = s.cast.slice();
      next[i] = { ...next[i], ...member };
      return { cast: next };
    }),
  removeCastMember: (id) => set((s) => ({ cast: s.cast.filter((c) => c.id !== id) })),
  setCharacterVoice: (castId, profileId) =>
    set((s) => ({ cast: s.cast.map((c) => (c.id === castId ? { ...c, profileId } : c)) })),

  setScript: (script) => set({ script }),
  setProjectMeta: (patch) => set((s) => ({ meta: { ...s.meta, ...patch } })), // I1 merge
  setLexicon: (lexicon) => set({ lexicon: { ...lexicon } }), // I3 replace
  setVoiceCast: (name, profileId) =>
    set((s) => {
      const next = { ...s.voiceCast };
      if (profileId) next[name] = profileId;
      else delete next[name]; // clearing a mapping removes it (→ default voice)
      return { voiceCast: next };
    }),
  setOutputPrefs: (patch) =>
    set((s) => ({
      // I2 merge
      outputFormat: patch.outputFormat ?? s.outputFormat,
      loudness: patch.loudness ?? s.loudness,
      defaultVoice: patch.defaultVoice !== undefined ? patch.defaultVoice : s.defaultVoice,
      language: patch.language ?? s.language,
    })),
  setLongformOverrides: (patch) => set((s) => ({ overrides: { ...s.overrides, ...patch } })),
  setCoverRef: (coverRef) => set({ coverRef: coverRef ? { ...coverRef } : null }),
  setLastOutput: (lastOutput) => set({ lastOutput, lastOutputScript: '', lastOutputChapters: [] }),
  setLastOutputSnapshot: (lastOutput, lastOutputScript, lastOutputChapters) =>
    set({
      lastOutput,
      lastOutputScript,
      lastOutputChapters: lastOutputChapters.map((chapter) => ({ ...chapter })),
    }),
  convertMode: (mode) => {
    if (mode !== 'stories' && mode !== 'audiobook') return; // G3
    if (get().projectMode === mode) return; // G2 idempotent
    set({ projectMode: mode }); // G1: flag only, content untouched
  },

  saveProject: (name) =>
    set((s) => {
      const id = s.currentProjectId || genProjectId();
      const ts = (() => {
        try {
          return Date.now();
        } catch {
          return 0;
        }
      })();
      const proj: LongformProject = {
        id,
        name: name || 'Untitled',
        mode: s.projectMode,
        cast: s.cast.map((c) => ({ ...c })),
        tracks: snapshotTracks(s.storyTracks),
        script: s.script,
        meta: { ...s.meta },
        lexicon: { ...s.lexicon },
        coverRef: s.coverRef ? { ...s.coverRef } : null,
        outputFormat: s.outputFormat,
        loudness: s.loudness,
        defaultVoice: s.defaultVoice,
        language: s.language,
        overrides: { ...s.overrides },
        voiceCast: { ...s.voiceCast },
        updatedAt: ts,
      };
      const exists = s.storyProjects.some((p) => p.id === id);
      return {
        storyProjects: exists
          ? s.storyProjects.map((p) => (p.id === id ? proj : p))
          : [...s.storyProjects, proj],
        currentProjectId: id,
      };
    }),

  loadProject: (id) => {
    const p = get().storyProjects.find((x) => x.id === id);
    if (!p) return; // E5: no-op when id missing
    set({
      storyTracks: (p.tracks || []).map((t) => ({ ...t })),
      cast: (p.cast || DEFAULT_CAST).map((c) => ({ ...c })),
      script: p.script ?? SLICE_DEFAULTS.script,
      meta: { ...(p.meta ?? SLICE_DEFAULTS.meta) },
      lexicon: { ...(p.lexicon ?? SLICE_DEFAULTS.lexicon) },
      coverRef: p.coverRef ? { ...p.coverRef } : SLICE_DEFAULTS.coverRef,
      outputFormat: p.outputFormat ?? SLICE_DEFAULTS.outputFormat,
      loudness: p.loudness ?? SLICE_DEFAULTS.loudness,
      defaultVoice: p.defaultVoice ?? SLICE_DEFAULTS.defaultVoice,
      language: p.language ?? SLICE_DEFAULTS.language,
      overrides: { ...SLICE_DEFAULTS.overrides, ...p.overrides },
      voiceCast: { ...(p.voiceCast ?? SLICE_DEFAULTS.voiceCast) },
      // A render belongs to the project it was made in — loading another
      // project must not present A's finished file as B's output (#1139).
      lastOutput: SLICE_DEFAULTS.lastOutput,
      lastOutputScript: SLICE_DEFAULTS.lastOutputScript,
      lastOutputChapters: [...SLICE_DEFAULTS.lastOutputChapters],
      projectMode: p.mode === 'audiobook' ? 'audiobook' : 'stories', // E3 default-safe
      currentProjectId: id,
    });
  },

  newProject: (mode = 'stories') =>
    set({
      storyTracks: [],
      cast: DEFAULT_CAST.map((c) => ({ ...c })),
      ...SLICE_DEFAULTS,
      meta: { ...SLICE_DEFAULTS.meta },
      lexicon: { ...SLICE_DEFAULTS.lexicon },
      overrides: { ...SLICE_DEFAULTS.overrides },
      voiceCast: { ...SLICE_DEFAULTS.voiceCast },
      projectMode: mode === 'audiobook' ? 'audiobook' : 'stories',
      currentProjectId: null,
    }),

  deleteProject: (id) =>
    set((s) => ({
      storyProjects: s.storyProjects.filter((p) => p.id !== id),
      currentProjectId: s.currentProjectId === id ? null : s.currentProjectId,
    })),
  renameProject: (id, name) =>
    set((s) => ({ storyProjects: s.storyProjects.map((p) => (p.id === id ? { ...p, name } : p)) })),
});

// ── Deprecated alias (one-PR bridge so the slice rename breaks no import) ──
export const createStoriesSlice = createLongformSlice;
