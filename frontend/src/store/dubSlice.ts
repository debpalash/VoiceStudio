/**
 * Dub pipeline slice — Phase 2.2 (App.jsx monolith reduction).
 *
 * This was the largest cluster of `useState` calls in App.jsx — the state
 * that drives the whole dubbing workflow: job id, progress, segments,
 * language, translate/generate settings. Moving it into the store lets deep
 * children read pipeline state without 30+ props threaded through.
 *
 * Setters mirror React's signature — each accepts either a value or an
 * `(prev) => next` updater so existing call sites (`setDubSegments(prev =>
 * prev.map(...))`) work unchanged. The `functionalSet` helper keeps that
 * surface tiny.
 *
 * Not persisted: dub state is transient per session. Project load / dub
 * history restore explicitly rehydrates the relevant fields.
 */
import type { StateCreator } from 'zustand';
import type { EffectPreset } from '../api/engines';
import { settleCueSources } from '../utils/segments';

type DubStep =
  | 'idle'
  | 'uploading'
  | 'installing-asr'
  | 'transcribing'
  | 'editing'
  | 'generating'
  | 'stopping'
  | 'done';

type DubPrepStage = 'download' | 'extract' | 'demucs' | 'scene' | null;

interface DubProgress {
  current: number;
  total: number;
  text: string;
}

/** Per-stage progress for the prep pipeline (download, demucs). */
interface DubPrepProgress {
  percent: number | null; // 0–100, or null if not known yet
  speedBps: number | null; // download speed in bytes/sec, when relevant
  etaS: number | null; // ETA in seconds, when known
  stageStartedAt: number | null; // ms epoch; used for elapsed-time display
}

/** Segments are a loose shape — many optional fields added over time. */
type DubSegment = Record<string, unknown> & { id: string; text: string };

/** One multi-language batch pick — display name + ISO code (MultiLangPicker). */
export interface MultiLangPick {
  lang: string;
  code: string;
}

type Updater<T> = T | ((prev: T) => T);

function resolve<T>(updater: Updater<T>, prev: T): T {
  return typeof updater === 'function' ? (updater as (prev: T) => T)(prev) : updater;
}

/** Structured pipeline failure (plan-04 #131) — carries the specific cause,
 *  an actionable hint, an optional docs-topic key, and a copyable diagnostic. */
interface DubFailure {
  reason: string;
  errorClass?: string;
  stage?: string;
  hint?: string;
  docsTopic?: string;
  diagnostic?: string;
}

export interface DubSlice {
  // ── Pipeline state ────────────────────────────────────────────────────
  dubJobId: string | null;
  dubStep: DubStep;
  dubTaskId: string | null;
  dubPrepStage: DubPrepStage;
  dubPrepProgress: DubPrepProgress;
  dubProgress: DubProgress;
  /** ID of the segment containing the current media playhead, or null. */
  dubCurrentSegId: string | null;
  dubError: string;
  dubFailure: DubFailure | null;
  isTranslating: boolean;

  // ── Content ───────────────────────────────────────────────────────────
  dubSegments: DubSegment[];
  dubTranscript: string;
  dubFilename: string;
  dubDuration: number;
  dubTracks: string[];

  // Bumped every time a generation completes. Cache-busts the dubbed
  // preview-video URL, which is otherwise identical across re-dubs — the
  // WebView could keep serving the previous dub after an edit + re-generate
  // (#281).
  dubGenNonce: number;

  // ── Language / translate ──────────────────────────────────────────────
  dubLang: string;
  dubLangCode: string;
  dubSourceLangCode: string;

  // Optional speaker-count hint passed to pyannote diarization (#274). null =
  // let pyannote auto-detect; a positive int forces that many speakers when
  // auto-detect collapses a multi-speaker clip to one.
  dubNumSpeakers: number | null;

  // Optional regional dialect for translation (#280), BCP-47 like "es-AR".
  // '' = default (no regional preference). Honored by LLM-backed translate
  // paths (OpenAI/Ollama provider or Cinematic quality).
  dubDialect: string;

  // Multi-language batch mode (P1.4) — the checkbox + language picks used by
  // the "Generate N dubs" loop. Lived in DubTab component state before, so a
  // tab switch or project reload silently dropped the picks; now they ride
  // the store and the project save/load payload.
  multiLangMode: boolean;
  multiLangs: MultiLangPick[];

  // ── Generation options ────────────────────────────────────────────────
  dubInstruct: string;
  preserveBg: boolean;
  defaultTrack: string;
  exportTracks: Record<string, boolean>;

  // Segment ids most recently rendered at num_step=8 (preview quality).
  // The client re-renders these at full quality before final export.
  previewSegIds: string[];

  // Path-free metadata for usable source-video voices. A source can be a
  // pooled per-speaker clone or a clean per-segment reference.
  speakerClones: Record<
    string,
    {
      ref_audio?: string;
      ref_text?: string;
      duration: number;
      source_count: number;
      kind?: 'speaker' | 'segment';
    }
  >;

  // ── Effect Presets ────────────────────────────────────────────────────
  segmentEffectPresets: Record<string, string>;
  setSegmentEffectPreset: (segId: string, presetId: string) => void;
  availableEffectPresets: EffectPreset[];
  setAvailableEffectPresets: (presets: EffectPreset[]) => void;

  /** #119: 'video' (default) or 'audio' — audio-only jobs skip video work. */
  dubInputType: 'video' | 'audio';

  // ── Setters (React-style; accept value or updater fn) ─────────────────
  setDubJobId: (v: Updater<string | null>) => void;
  setDubStep: (v: Updater<DubStep>) => void;
  setDubInputType: (v: Updater<'video' | 'audio'>) => void;
  setDubTaskId: (v: Updater<string | null>) => void;
  setDubPrepStage: (v: Updater<DubPrepStage>) => void;
  setDubPrepProgress: (v: Updater<DubPrepProgress>) => void;
  setDubProgress: (v: Updater<DubProgress>) => void;
  setDubCurrentSegId: (v: Updater<string | null>) => void;
  setDubError: (v: Updater<string>) => void;
  setDubFailure: (v: Updater<DubSlice['dubFailure']>) => void;
  setIsTranslating: (v: Updater<boolean>) => void;
  setDubSegments: (v: Updater<DubSegment[]>) => void;
  setDubTranscript: (v: Updater<string>) => void;
  setDubFilename: (v: Updater<string>) => void;
  setDubDuration: (v: Updater<number>) => void;
  setDubTracks: (v: Updater<string[]>) => void;
  bumpDubGenNonce: () => void;
  setDubLang: (v: Updater<string>) => void;
  setDubLangCode: (v: Updater<string>) => void;
  setDubSourceLangCode: (v: Updater<string>) => void;
  /**
   * User-driven target-language switch (P1.2). Unlike the plain setter it
   * also remaps segment text through the per-language `translations` store:
   * the outgoing language's text is snapshotted into `translations[prev]`
   * (only when it's an actual translation — differs from `text_original`),
   * and the incoming language's saved text is swapped into `text` when one
   * exists. Non-destructive: with no saved entry, `text` is left untouched —
   * exactly the legacy behaviour. Restore/rehydrate paths (project load, dub
   * history) must keep using `setDubLangCode`, which never touches segments.
   */
  switchDubLangCode: (code: string) => void;
  setDubNumSpeakers: (v: Updater<number | null>) => void;
  setDubDialect: (v: Updater<string>) => void;
  setMultiLangMode: (v: Updater<boolean>) => void;
  setMultiLangs: (v: Updater<MultiLangPick[]>) => void;
  setDubInstruct: (v: Updater<string>) => void;
  setPreserveBg: (v: Updater<boolean>) => void;
  setDefaultTrack: (v: Updater<string>) => void;
  setExportTracks: (v: Updater<Record<string, boolean>>) => void;
  setPreviewSegIds: (v: Updater<string[]>) => void;
  setSpeakerClones: (v: Updater<DubSlice['speakerClones']>) => void;

  /** Reset every pipeline field back to idle defaults. */
  resetDubState: () => void;
}

const INITIAL: Omit<
  DubSlice,
  | 'setDubJobId'
  | 'setDubStep'
  | 'setDubInputType'
  | 'setDubTaskId'
  | 'setDubPrepStage'
  | 'setDubPrepProgress'
  | 'setDubCurrentSegId'
  | 'setDubProgress'
  | 'setDubError'
  | 'setDubFailure'
  | 'setIsTranslating'
  | 'setDubSegments'
  | 'setDubTranscript'
  | 'setDubFilename'
  | 'setDubDuration'
  | 'setDubTracks'
  | 'bumpDubGenNonce'
  | 'setDubLang'
  | 'setDubLangCode'
  | 'setDubSourceLangCode'
  | 'switchDubLangCode'
  | 'setDubNumSpeakers'
  | 'setDubDialect'
  | 'setMultiLangMode'
  | 'setMultiLangs'
  | 'setDubInstruct'
  | 'setPreserveBg'
  | 'setDefaultTrack'
  | 'setExportTracks'
  | 'setPreviewSegIds'
  | 'setSpeakerClones'
  | 'setSegmentEffectPreset'
  | 'setAvailableEffectPresets'
  | 'resetDubState'
> = {
  dubJobId: null,
  dubStep: 'idle',
  dubInputType: 'video',
  dubTaskId: null,
  dubPrepStage: null,
  dubPrepProgress: { percent: null, speedBps: null, etaS: null, stageStartedAt: null },
  dubProgress: { current: 0, total: 0, text: '' },
  dubCurrentSegId: null,
  dubError: '',
  dubFailure: null,
  isTranslating: false,
  dubSegments: [],
  dubTranscript: '',
  dubFilename: '',
  dubDuration: 0,
  dubTracks: [],
  dubGenNonce: 0,
  dubLang: 'Auto',
  dubLangCode: 'en',
  dubSourceLangCode: 'auto',
  dubNumSpeakers: null,
  dubDialect: '',
  multiLangMode: false,
  multiLangs: [],
  dubInstruct: '',
  preserveBg: true,
  // Empty means "the selected/first dubbed language". Original remains an
  // explicit choice in the export controls (#1575).
  defaultTrack: '',
  exportTracks: { original: true },
  previewSegIds: [],
  speakerClones: {},
  segmentEffectPresets: {},
  availableEffectPresets: [],
};

export const createDubSlice: StateCreator<DubSlice, [], [], DubSlice> = (set, get) => ({
  ...INITIAL,

  setDubJobId: (v) => set((s) => ({ dubJobId: resolve(v, s.dubJobId) })),
  setDubStep: (v) => set((s) => ({ dubStep: resolve(v, s.dubStep) })),
  setDubInputType: (v) => set((s) => ({ dubInputType: resolve(v, s.dubInputType) })),
  setDubTaskId: (v) => set((s) => ({ dubTaskId: resolve(v, s.dubTaskId) })),
  setDubPrepStage: (v) => set((s) => ({ dubPrepStage: resolve(v, s.dubPrepStage) })),
  setDubPrepProgress: (v) => set((s) => ({ dubPrepProgress: resolve(v, s.dubPrepProgress) })),
  setDubProgress: (v) => set((s) => ({ dubProgress: resolve(v, s.dubProgress) })),
  setDubCurrentSegId: (v) => set((s) => ({ dubCurrentSegId: resolve(v, s.dubCurrentSegId) })),
  setDubError: (v) => set((s) => ({ dubError: resolve(v, s.dubError) })),
  setDubFailure: (v) => set((s) => ({ dubFailure: resolve(v, s.dubFailure) })),
  setIsTranslating: (v) => set((s) => ({ isTranslating: resolve(v, s.isTranslating) })),
  // An imported cue whose text no longer matches is dropped for good here,
  // so no writer can revive it by landing on equal text later (#2295).
  setDubSegments: (v) => set((s) => ({ dubSegments: settleCueSources(resolve(v, s.dubSegments)) })),
  setDubTranscript: (v) => set((s) => ({ dubTranscript: resolve(v, s.dubTranscript) })),
  setDubFilename: (v) => set((s) => ({ dubFilename: resolve(v, s.dubFilename) })),
  setDubDuration: (v) => set((s) => ({ dubDuration: resolve(v, s.dubDuration) })),
  setDubTracks: (v) => set((s) => ({ dubTracks: resolve(v, s.dubTracks) })),
  bumpDubGenNonce: () => set(() => ({ dubGenNonce: Date.now() })),
  setDubLang: (v) => set((s) => ({ dubLang: resolve(v, s.dubLang) })),
  setDubLangCode: (v) => set((s) => ({ dubLangCode: resolve(v, s.dubLangCode) })),
  setDubSourceLangCode: (v) => set((s) => ({ dubSourceLangCode: resolve(v, s.dubSourceLangCode) })),
  switchDubLangCode: (code) =>
    set((s) => {
      const prev = s.dubLangCode;
      if (!code || code === prev) return {};
      const switched = s.dubSegments.map((seg) => {
        const translations: Record<string, string> = {
          ...(seg.translations as Record<string, string> | undefined),
        };
        // Snapshot the outgoing language's text — but only real translations
        // (differs from the source), so a never-translated row can't stamp
        // source-language text as the previous language's translation.
        // Legacy projects (no `translations` yet) get theirs seeded here.
        const text = typeof seg.text === 'string' ? seg.text : '';
        if (prev && text.trim() && text !== seg.text_original) translations[prev] = text;
        const incoming = translations[code];
        return {
          ...seg,
          translations,
          ...(typeof incoming === 'string' && incoming.trim()
            ? { text: incoming, merge_parts: undefined }
            : {}),
        };
      });
      // The dropdown paths each cleared a stale dialect by hand; doing it
      // here means EVERY caller (dropdown, multi-language loop, the Export
      // preview tabs) inherits the guard. Same predicate as
      // api/dialects.dialectMatchesLang, inlined to keep the store slice
      // dependency-free: a dialect "es-AR" only survives a switch to a
      // language whose base code it extends.
      const base = code.toLowerCase().split('-')[0];
      const dialectStillValid = !!s.dubDialect && s.dubDialect.toLowerCase().startsWith(`${base}-`);
      return {
        dubLangCode: code,
        dubSegments: settleCueSources(switched),
        ...(dialectStillValid ? {} : { dubDialect: '' }),
      };
    }),
  setDubNumSpeakers: (v) => set((s) => ({ dubNumSpeakers: resolve(v, s.dubNumSpeakers) })),
  setDubDialect: (v) => set((s) => ({ dubDialect: resolve(v, s.dubDialect) })),
  setMultiLangMode: (v) => set((s) => ({ multiLangMode: resolve(v, s.multiLangMode) })),
  setMultiLangs: (v) => set((s) => ({ multiLangs: resolve(v, s.multiLangs) })),
  setDubInstruct: (v) => set((s) => ({ dubInstruct: resolve(v, s.dubInstruct) })),
  setPreserveBg: (v) => set((s) => ({ preserveBg: resolve(v, s.preserveBg) })),
  setDefaultTrack: (v) => set((s) => ({ defaultTrack: resolve(v, s.defaultTrack) })),
  setExportTracks: (v) => set((s) => ({ exportTracks: resolve(v, s.exportTracks) })),
  setPreviewSegIds: (v) => set((s) => ({ previewSegIds: resolve(v, s.previewSegIds) })),
  setSpeakerClones: (v) => set((s) => ({ speakerClones: resolve(v, s.speakerClones) })),
  setSegmentEffectPreset: (segId, presetId) =>
    set((s) => ({
      segmentEffectPresets: { ...s.segmentEffectPresets, [segId]: presetId },
    })),
  setAvailableEffectPresets: (presets) => set({ availableEffectPresets: presets }),

  resetDubState: () => {
    // Touch `get` so strict-mode double-invocation of the initializer doesn't
    // warn us about unused args — and future logging can read current state.
    void get;
    set(INITIAL);
  },
});
