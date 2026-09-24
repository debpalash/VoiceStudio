import { startTranslationRun, appendTranslationLog, updateTranslationRun, finishTranslationRun } from './translation-activity';
import type { DubExportPreferences } from './dub-export';
import {
  MAX_COOKIE_EXPORT_BYTES,
  _cookieTransportAllowed,
} from '../../../../../../frontend/src/utils/cookieExport';
import { projectSession, type DubProject } from '../projects/project-format';
import { DUB_DRAFT_KEY, restoreDubDraft } from './dub-draft';
import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import { apiJson, ApiError } from '@/lib/api/client';
import { describeDubTranslationError } from './translation-error';
import { beginAppActivity } from '@/lib/app-activity';
import { recordActionBreadcrumb } from '@/lib/report-breadcrumb';
import { consumeTaskStream, type TaskEvent } from '@/lib/api/event-stream';
import { publicFailureFromEvent, type PublicFailure } from '@/lib/api/failure';
import {
  assignSpeakerProfile,
  applySpeakerCloneDefaults,
  cueSourceId,
  segmentGenInputs,
  settleCueSources,
  withOriginalCueSource,
  withoutCueSource,
} from '../../../../../../frontend/src/utils/segments';
import { hasCompleteTranslation } from '../../../../../../frontend/src/utils/multiLang';
import {
  ATTRIBUTION_FIELDS,
  applyAttribution,
  attributionAt,
  clipParts,
  insertionSlot,
  keepParts,
  mergedOriginalParts,
  mergedParts,
  nextSegmentId,
  partsFor,
  type SegmentPart,
} from '../../../../../../frontend/src/utils/segmentParts';
import { commitMoveResize } from '../../../../../../frontend/src/utils/timeline';
import type { PasteTranslationRow } from '../../../../../../frontend/src/utils/pasteTranslations';
import type {
  DubAgentTranslationRequest,
  DubAgentTranslationResult,
  RepairAgentId,
} from '../../../../preload/index.d';
export interface DubSegment {
  gain?: number;
  speed?: number;
  direction?: string;
  id: string;
  start: number;
  end: number;
  text: string;
  text_original: string;
  speaker_id?: string;
  profile_id?: string;
  instruct?: string;
  target_lang?: string;
  translations?: Record<string, string>;
  /** The cue a caption import wrote, kept for unchanged exports. */
  webvtt_source?: { id?: string; text: string; cue: string };
  srt_source?: { id?: string; text: string; cue: string };
  /** Set while `text` is still that import's words; cleared by any other write. */
  cue_source_id?: string;
  merge_parts?: SegmentPart[];
  merge_parts_original?: SegmentPart[];
  original_duration?: number;
  qc_drift?: number;
  qc_flagged?: boolean;
  qc_recognized?: string;
  qc_measured_start?: number;
  qc_measured_end?: number;
  translate_error?: string;
  translate_errors?: Record<string, string>;
  translation_skipped?: boolean;
  translate_degraded?: string;
  translate_literal?: string;
  translate_critique?: string;
  rate_ratio?: number;
  rate_error?: string;
  plan?: {
    status: 'fits' | 'tight' | 'impossible';
    est_dur_s: number;
    available_s: number;
    est_overrun_s: number;
    calibrated?: boolean;
    suggested_text?: string;
    suggested_est_dur_s?: number;
  };
  sync_ratio?: number;
  fit_status?: {
    status: 'fits' | 'overflows' | 'video_stretched' | 'audio_slowed';
    overflow_s?: number;
    stretch_ratio?: number;
    audio_rate?: number;
  };
  /** Set only on untouched Agent translations; direct edits clear it. */
  agent_generated_lang?: string;
  /** Every stored translation that still contains untouched Agent output. */
  agent_generated_langs?: string[];
}
export type DubTiming = 'concise' | 'smart_fit' | 'stretch_video' | 'strict_slot';
export interface AsrModelMissing {
  error: 'asr_model_missing';
  detail?: string;
  missing_repo_id?: string;
  recommended?: { repo_id: string; label?: string; size_gb?: number };
}
export interface DubSession {
  exportOptions?: DubExportPreferences;
  fitOptions?: {
    max_audio_only_rate?: number;
    audio_rate_cap?: number;
    video_slow_cap?: number;
    gap_guard_s?: number;
    allow_video_retime?: boolean;
  };
  steps?: number;
  guidance?: number;
  speed?: number;
  instruct?: string;
  sourceLanguage?: string;
  numSpeakers?: number;
  timingStrategy?: DubTiming;
  voiceMatch?: 'per_line' | 'consistent';
  generatedTiming?: DubTiming;
  pendingTiming?: DubTiming;
  fingerprintsByLang?: Record<string, Record<string, string>>;
  project?: DubProject;
  translationFallback?: boolean;
  subtitleImport?: { file: string; stats: Record<string, number> };
  recovery: 'preparing' | 'generating' | 'transcribing' | null;
  target: string;
  multiTargets?: Array<{ lang: string; code: string }>;
  batchProgress?: { current: number; total: number; language: string };
  quality: 'fast' | 'autofit' | 'cinematic' | 'agent';
  agentCli?: RepairAgentId;
  agentPass?: {
    stage: 'measuring' | 'adapting' | 'rerendering';
    pass: number;
    remaining: number;
  };
  autoGlossary?: boolean;
  reflectPass?: boolean;
  condenseSuggest?: boolean;
  dialect?: string;
  translationInstructions?: string;
  jobId: string | null;
  taskId: string | null;
  filename: string;
  duration?: number;
  inputType: 'audio' | 'video';
  phase:
    | 'idle'
    | 'preparing'
    | 'transcribing'
    | 'editing'
    | 'translating'
    | 'generating'
    | 'done'
    | 'importing'
    | 'cleaning';
  segments: DubSegment[];
  sourceLang: string;
  tracks: string[];
  event: TaskEvent | null;
  error: string | null;
  failure?: PublicFailure | null;
  asrModelMissing?: AsrModelMissing | null;
}
const initial: DubSession = {
  recovery: null,
  target: 'Spanish',
  multiTargets: [],
  quality: 'fast',
  autoGlossary: true,
  reflectPass: true,
  condenseSuggest: false,
  timingStrategy: 'strict_slot',
  voiceMatch: 'per_line',
  fingerprintsByLang: {},
  jobId: null,
  taskId: null,
  filename: '',
  duration: 0,
  inputType: 'video',
  phase: 'idle',
  segments: [],
  sourceLang: '',
  tracks: [],
  event: null,
  error: null,
  failure: null,
  asrModelMissing: null,
};
export const DUB_STOP_FAILED = 'DUB_STOP_FAILED';
export const DUB_AGENT_UNAVAILABLE = 'DUB_AGENT_UNAVAILABLE';
const AGENT_FIT_BLOCKING_ERRORS = new Set(['no-llm', 'fit-provider-failed']);
class DubAsrModelMissingError extends Error {
  constructor(readonly payload: AsrModelMissing) {
    super(payload.detail || payload.error);
    this.name = 'DubAsrModelMissingError';
  }
}
function readDraft() {
  try {
    return restoreDubDraft(localStorage.getItem(DUB_DRAFT_KEY), initial);
  } catch {
    return initial;
  }
}
export const dubSession = new Store<DubSession>(readDraft());
let persistTimer: ReturnType<typeof setTimeout> | null = null;
let persistenceSuspended = false;
const persist = () => {
  if (persistenceSuspended) return;
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = null;
  try {
    localStorage.setItem(
      DUB_DRAFT_KEY,
      JSON.stringify({
        ...dubSession.state,
        event: null,
        error: null,
        failure: null,
        asrModelMissing: null,
      }),
    );
  } catch {
    /* Current work remains available in memory. */
  }
};

export function flushDubDraft(): void {
  persist();
}

/** Stop the current store and pagehide handler from recreating a confirmed reset draft. */
export function clearDubDraftForReset(): void {
  persistenceSuspended = true;
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = null;
  localStorage.removeItem(DUB_DRAFT_KEY);
}
let previousDraft = dubSession.state;
const unsubscribeDraft = dubSession.subscribe(() => {
  const current = dubSession.state;
  if (
    current.phase !== previousDraft.phase ||
    current.taskId !== previousDraft.taskId ||
    current.recovery !== previousDraft.recovery
  )
    persist();
  else if (
    current.segments !== previousDraft.segments ||
    current.target !== previousDraft.target ||
    current.multiTargets !== previousDraft.multiTargets ||
    current.quality !== previousDraft.quality ||
    current.autoGlossary !== previousDraft.autoGlossary ||
    current.reflectPass !== previousDraft.reflectPass ||
    current.condenseSuggest !== previousDraft.condenseSuggest ||
    current.dialect !== previousDraft.dialect ||
    current.translationInstructions !== previousDraft.translationInstructions ||
    current.exportOptions !== previousDraft.exportOptions ||
    current.timingStrategy !== previousDraft.timingStrategy ||
    current.voiceMatch !== previousDraft.voiceMatch ||
    current.sourceLanguage !== previousDraft.sourceLanguage ||
    current.numSpeakers !== previousDraft.numSpeakers ||
    current.steps !== previousDraft.steps ||
    current.guidance !== previousDraft.guidance ||
    current.speed !== previousDraft.speed ||
    current.instruct !== previousDraft.instruct ||
    current.duration !== previousDraft.duration ||
    current.tracks !== previousDraft.tracks
  ) {
    if (persistTimer) clearTimeout(persistTimer);
    persistTimer = setTimeout(persist, 250);
  }
  previousDraft = current;
});
if (typeof window !== 'undefined') window.addEventListener('pagehide', persist);
if (import.meta.hot)
  import.meta.hot.dispose(() => {
    persist();
    unsubscribeDraft.unsubscribe();
    window.removeEventListener('pagehide', persist);
  });
export const setDubProduction = (
  value: Pick<
    DubSession,
    | 'timingStrategy'
    | 'voiceMatch'
    | 'sourceLanguage'
    | 'numSpeakers'
    | 'steps'
    | 'guidance'
    | 'speed'
    | 'instruct'
  >,
) => {
  if (['idle', 'editing', 'done'].includes(dubSession.state.phase) && !dubSession.state.recovery)
    patch(value);
};
export const setDubExportPreferences = (value: Partial<DubExportPreferences>) => {
  if (!controller && !cancelling && !dubSession.state.recovery)
    patch({ exportOptions: { ...dubSession.state.exportOptions, ...value } });
};
export const setDubQuality = (quality: DubSession['quality']) => {
  if (['idle', 'editing', 'done'].includes(dubSession.state.phase) && !dubSession.state.recovery)
    patch({ quality, ...(quality === 'agent' ? {} : { agentCli: undefined }) });
};
export const setDubTranslationOptions = (
  value: Pick<Partial<DubSession>, 'autoGlossary' | 'reflectPass' | 'condenseSuggest' | 'dialect' | 'translationInstructions'>,
) => {
  if (['idle', 'editing', 'done'].includes(dubSession.state.phase) && !dubSession.state.recovery)
    patch(value);
};
export const setDubTarget = (target: string, code?: string) =>
  dubSession.setState((current) => ({
    ...current,
    target,
    segments: code
      ? current.segments.map((segment) => {
          const translated = segment.translations?.[code];
          const next = {
            ...segment,
            text: translated || segment.text_original || segment.text,
            agent_generated_lang: segment.agent_generated_langs?.includes(code) ? code : undefined,
          };
          // Back on the untouched original, the row shows the import's words
          // again, so its caption markup applies once more (#2295).
          return translated ? withoutCueSource(next) : withOriginalCueSource(next);
        })
      : current.segments,
  }));
export const setDubMultiTargets = (multiTargets: Array<{ lang: string; code: string }>) => {
  if (!['idle', 'editing', 'done'].includes(dubSession.state.phase) || dubSession.state.recovery)
    return;
  patch({
    multiTargets: multiTargets.filter(
      (item, index, all) =>
        /^[a-zA-Z]{2,3}(-[a-zA-Z0-9]+)?$/.test(item.code) &&
        item.lang.trim().length > 0 &&
        all.findIndex((candidate) => candidate.code === item.code) === index,
    ),
  });
};
export const useDubSession = () => useStore(dubSession);
/** Reactive mirror of the module-level cancel-in-flight flag. Ephemeral by
 * design: never persisted, so a reload can never wedge the UI disabled. */
export const dubCancelling = new Store(false);
export const useDubCancelling = () => useStore(dubCancelling);
const editHistory = new Store({ undoDepth: 0, redoDepth: 0 });
const undoStack: DubSegment[][] = [];
const redoStack: DubSegment[][] = [];
const MAX_EDIT_HISTORY = 50;
const EDIT_HISTORY_COALESCE_MS = 800;
let lastHistoryGroup: string | null = null;
let lastHistoryEditAt = 0;
const MERGE_PART_FIELDS = new Set<string>(['text', ...ATTRIBUTION_FIELDS]);
const ATTRIBUTION_FIELD_NAMES = new Set<string>(ATTRIBUTION_FIELDS);
const TRANSLATION_PLAN_INVALIDATING_FIELDS = new Set([
  'text',
  'start',
  'end',
  'speed',
  'target_lang',
]);

const cloneSegments = (segments: DubSegment[]) =>
  segments.map((segment) => ({
    ...segment,
    translations: segment.translations ? { ...segment.translations } : undefined,
    agent_generated_langs: segment.agent_generated_langs
      ? [...segment.agent_generated_langs]
      : undefined,
    merge_parts: segment.merge_parts?.map((part) => ({ ...part })),
    merge_parts_original: segment.merge_parts_original?.map((part) => ({
      ...part,
    })),
  }));
const publishEditHistory = () =>
  editHistory.setState(() => ({
    undoDepth: undoStack.length,
    redoDepth: redoStack.length,
  }));
const editingAllowed = () =>
  !controller &&
  !cancelling &&
  !dubSession.state.recovery &&
  ['editing', 'done'].includes(dubSession.state.phase);
const resetHistoryGroup = () => {
  lastHistoryGroup = null;
  lastHistoryEditAt = 0;
};
const commitSegmentEdit = (next: DubSegment[], historyGroup?: string) => {
  if (!editingAllowed() || next === dubSession.state.segments) return false;
  const now = Date.now();
  const coalesces = Boolean(
    historyGroup &&
    historyGroup === lastHistoryGroup &&
    now - lastHistoryEditAt <= EDIT_HISTORY_COALESCE_MS &&
    undoStack.length,
  );
  if (!coalesces) {
    undoStack.push(cloneSegments(dubSession.state.segments));
    if (undoStack.length > MAX_EDIT_HISTORY) undoStack.shift();
  }
  lastHistoryGroup = historyGroup || null;
  lastHistoryEditAt = historyGroup ? now : 0;
  redoStack.length = 0;
  patch({ segments: next });
  publishEditHistory();
  return true;
};

export const useDubEditHistory = () => useStore(editHistory);
export function clearDubEditHistory() {
  undoStack.length = 0;
  redoStack.length = 0;
  resetHistoryGroup();
  publishEditHistory();
}
export function undoDubEdit() {
  if (!editingAllowed()) return;
  resetHistoryGroup();
  const previous = undoStack.pop();
  if (!previous) return;
  redoStack.push(cloneSegments(dubSession.state.segments));
  patch({ segments: previous });
  publishEditHistory();
}
export function redoDubEdit() {
  if (!editingAllowed()) return;
  resetHistoryGroup();
  const next = redoStack.pop();
  if (!next) return;
  undoStack.push(cloneSegments(dubSession.state.segments));
  patch({ segments: next });
  publishEditHistory();
}
let controller: AbortController | null = null;
let cancelling = false;
let batchRunId = 0;
const patch = (value: Partial<DubSession>) =>
  dubSession.setState((current) => ({
    ...current,
    ...value,
    // An imported cue whose text no longer matches is dropped for good, so
    // no writer can revive it by landing on equal text later (#2295).
    ...(value.segments ? { segments: settleCueSources(value.segments) } : {}),
  }));
const QC_INVALIDATING_FIELDS = new Set([
  'text',
  'profile_id',
  'instruct',
  'speed',
  'gain',
  'target_lang',
  'direction',
  'start',
  'end',
]);
const invalidateQc = (segment: DubSegment): DubSegment => {
  const next = { ...segment };
  delete next.qc_drift;
  delete next.qc_flagged;
  delete next.qc_recognized;
  delete next.qc_measured_start;
  delete next.qc_measured_end;
  return next;
};
const patchSegment = (segment: DubSegment, value: Partial<DubSegment>) => {
  const fields = Object.keys(value);
  // Any text write (edit, paste) replaces the import's words, even when equal.
  const base = 'text' in value ? withoutCueSource(segment) : segment;
  const next = fields.some((field) => QC_INVALIDATING_FIELDS.has(field))
    ? { ...invalidateQc(base), ...value, id: segment.id }
    : { ...base, ...value, id: segment.id };
  if (segment.merge_parts && fields.some((field) => MERGE_PART_FIELDS.has(field))) {
    next.merge_parts = undefined;
    if (fields.some((field) => ATTRIBUTION_FIELD_NAMES.has(field)))
      next.merge_parts_original = undefined;
  }
  if (fields.some((field) => TRANSLATION_PLAN_INVALIDATING_FIELDS.has(field))) {
    delete next.plan;
    delete next.rate_ratio;
    delete next.rate_error;
    delete next.translate_error;
    delete next.translate_degraded;
    delete next.translate_literal;
    delete next.translate_critique;
    if (segment.agent_generated_lang && next.agent_generated_langs) {
      const untouched = next.agent_generated_langs.filter(
        (language) => language !== segment.agent_generated_lang,
      );
      next.agent_generated_langs = untouched.length ? untouched : undefined;
    }
    delete next.agent_generated_lang;
  }
  if (fields.some((field) => QC_INVALIDATING_FIELDS.has(field))) {
    delete next.sync_ratio;
    delete next.fit_status;
  }
  return next;
};

export const editDubSegment = (
  id: string,
  value: Partial<DubSegment>,
  options?: { historyGroup?: string },
) => {
  const current = dubSession.state.segments.find((segment) => segment.id === id);
  if (
    !current ||
    !Object.entries(value).some(([key, item]) => current[key as keyof DubSegment] !== item)
  )
    return;
  commitSegmentEdit(
    dubSession.state.segments.map((segment) =>
      segment.id === id ? patchSegment(segment, value) : segment,
    ),
    options?.historyGroup,
  );
};

export function editDubSegments(ids: ReadonlySet<string>, value: Partial<DubSegment>) {
  if (!ids.size) return;
  const next = dubSession.state.segments.map((segment) =>
    ids.has(segment.id) ? patchSegment(segment, value) : segment,
  );
  if (
    !next.some((segment, index) =>
      Object.keys(value).some(
        (key) =>
          segment[key as keyof DubSegment] !==
          dubSession.state.segments[index][key as keyof DubSegment],
      ),
    )
  )
    return;
  commitSegmentEdit(next);
}

export function assignDubSpeakerProfile(speakerId: string, profileId: string) {
  const current = dubSession.state.segments;
  const assigned = assignSpeakerProfile(current, speakerId, profileId) as DubSegment[];
  let changed = false;
  const next = assigned.map((segment, index) => {
    if (segment === current[index]) return segment;
    changed = true;
    const invalidated = invalidateQc(segment);
    delete invalidated.sync_ratio;
    delete invalidated.fit_status;
    return invalidated;
  });
  if (changed) commitSegmentEdit(next);
}

export function applyDubTranslationRows(targetCode: string, rows: PasteTranslationRow[]) {
  if (!targetCode || !rows.length) return false;
  const mapped = new Map(
    rows
      .filter((row) => row.matched && typeof row.after === 'string' && row.after.trim())
      .map((row) => [row.id, row.after!.trim()]),
  );
  if (!mapped.size) return false;
  let changed = false;
  const next = dubSession.state.segments.map((segment) => {
    const text = mapped.get(segment.id);
    if (!text || (segment.text === text && segment.translations?.[targetCode] === text))
      return segment;
    changed = true;
    return patchSegment(segment, {
      text,
      translations: { ...segment.translations, [targetCode]: text },
    });
  });
  return changed ? commitSegmentEdit(next) : false;
}

export function deleteDubSegment(id: string) {
  if (!dubSession.state.segments.some((segment) => segment.id === id)) return;
  commitSegmentEdit(dubSession.state.segments.filter((segment) => segment.id !== id));
}

export function deleteDubSegments(ids: ReadonlySet<string>) {
  if (!ids.size) return;
  const next = dubSession.state.segments.filter((segment) => !ids.has(segment.id));
  if (next.length === dubSession.state.segments.length) return;
  commitSegmentEdit(next);
}

export function skipFailedDubTranslations(target: string) {
  if (!target) return;
  let changed = false;
  const next = dubSession.state.segments.map((segment) => {
    const errors = { ...segment.translate_errors };
    const failed = Boolean(
      errors[target] || (!segment.translate_errors && segment.translate_error),
    );
    if (!failed) return segment;
    changed = true;
    delete errors[target];
    return {
      ...segment,
      translate_error: undefined,
      translate_errors: Object.keys(errors).length ? errors : undefined,
      translation_skipped: true,
    };
  });
  if (changed) commitSegmentEdit(next);
}

export function restoreDubSegments() {
  let changed = false;
  const next = dubSession.state.segments.map((segment) => {
    const text = segment.text_original || segment.text;
    if (
      text === segment.text &&
      !segment.translate_error &&
      !segment.translate_degraded &&
      !segment.translation_skipped
    )
      return segment;
    changed = true;
    return patchSegment(segment, {
      text,
      translate_error: undefined,
      translate_degraded: undefined,
      translation_skipped: undefined,
    });
  });
  if (changed) commitSegmentEdit(next);
}

export function insertDubSegment(id: string) {
  const segments = dubSession.state.segments;
  const index = segments.findIndex((segment) => segment.id === id);
  if (index < 0) return;
  const previous = segments[index];
  const slot = insertionSlot(previous, segments[index + 1]);
  const created: DubSegment = {
    id: nextSegmentId(
      segments.map((segment) => segment.id),
      previous.id,
    ),
    start: slot.start,
    end: slot.end,
    text: '',
    text_original: '',
  };
  for (const field of ['speaker_id', 'profile_id', 'target_lang'] as const) {
    if (previous[field] !== undefined) created[field] = previous[field];
  }
  commitSegmentEdit([...segments.slice(0, index + 1), created, ...segments.slice(index + 1)]);
}

const trimmedTextRange = (text: string, from: number, to: number) => {
  const raw = text.slice(from, to);
  return {
    from: from + raw.length - raw.trimStart().length,
    to: from + raw.trimEnd().length,
    text: raw.trim(),
  };
};

export function splitDubSegment(id: string, cursorPosition: number) {
  const segments = dubSession.state.segments;
  const index = segments.findIndex((segment) => segment.id === id);
  if (index < 0) return;
  const segment = segments[index];
  const text = segment.text || '';
  if (text.length < 2) return;
  const position = Math.max(1, Math.min(cursorPosition, text.length - 1));
  const midpoint = segment.start + (segment.end - segment.start) * (position / text.length);
  const parts = partsFor(segment);
  const leftRange = trimmedTextRange(text, 0, position);
  const rightRange = trimmedTextRange(text, position, text.length);
  if (!leftRange.text || !rightRange.text) return;
  const left = invalidateQc(
    applyAttribution(
      {
        ...segment,
        id: `${segment.id}_a`,
        text: leftRange.text,
        text_original: leftRange.text,
        end: midpoint,
        translations: undefined,
        agent_generated_lang: undefined,
        agent_generated_langs: undefined,
        merge_parts: keepParts(clipParts(parts, leftRange.from, leftRange.to)),
        merge_parts_original: keepParts(clipParts(parts, leftRange.from, leftRange.to)),
      },
      attributionAt(parts, leftRange.from),
    ) as DubSegment,
  );
  const right = invalidateQc(
    applyAttribution(
      {
        ...segment,
        id: `${segment.id}_b`,
        text: rightRange.text,
        text_original: rightRange.text,
        start: midpoint,
        translations: undefined,
        agent_generated_lang: undefined,
        agent_generated_langs: undefined,
        merge_parts: keepParts(clipParts(parts, rightRange.from, rightRange.to)),
        merge_parts_original: keepParts(clipParts(parts, rightRange.from, rightRange.to)),
      },
      attributionAt(parts, rightRange.from),
    ) as DubSegment,
  );
  commitSegmentEdit([...segments.slice(0, index), left, right, ...segments.slice(index + 1)]);
}

export function mergeDubSegment(id: string, direction: 'prev' | 'next' = 'next') {
  const segments = dubSession.state.segments;
  const selected = segments.findIndex((segment) => segment.id === id);
  const index = direction === 'prev' ? selected - 1 : selected;
  if (selected < 0 || index < 0 || index >= segments.length - 1) return;
  const first = segments[index];
  const second = segments[index + 1];
  const translations: Record<string, string> = {};
  for (const language of Object.keys(first.translations || {})) {
    if (typeof second.translations?.[language] === 'string')
      translations[language] =
        `${first.translations![language]} ${second.translations[language]}`.trim();
  }
  const merged: DubSegment = invalidateQc({
    ...first,
    text: `${first.text || ''} ${second.text || ''}`.trim(),
    text_original:
      `${first.text_original || first.text || ''} ${second.text_original || second.text || ''}`.trim(),
    end: second.end,
    translations: Object.keys(translations).length ? translations : undefined,
    agent_generated_lang: undefined,
    agent_generated_langs: undefined,
    merge_parts: mergedParts(first, second),
    merge_parts_original: mergedOriginalParts(first, second),
  });
  commitSegmentEdit([...segments.slice(0, index), merged, ...segments.slice(index + 2)]);
}

export function moveResizeDubSegment(id: string, timing: { start: number; end: number }) {
  const segments = dubSession.state.segments;
  const segment = segments.find((item) => item.id === id);
  if (
    !segment ||
    !Number.isFinite(timing.start) ||
    !Number.isFinite(timing.end) ||
    timing.start < 0 ||
    timing.end <= timing.start ||
    (segment.start === timing.start && segment.end === timing.end)
  )
    return;
  commitSegmentEdit(
    segments.map((item) =>
      item.id === id
        ? invalidateQc({
            ...(commitMoveResize(item, timing) as DubSegment),
            agent_generated_lang: undefined,
            agent_generated_langs: undefined,
          })
        : item,
    ),
  );
}

export function applyDubQc(
  results: Array<{
    seg_id: string;
    drift: number;
    flagged: boolean;
    recognized_text: string;
    measured_start?: number | null;
    measured_end?: number | null;
  }>,
) {
  const byId = new Map(results.map((result) => [String(result.seg_id), result]));
  patch({
    segments: dubSession.state.segments.map((segment, index) => {
      const result = byId.get(String(segment.id)) || byId.get(String(index));
      if (!result) return segment;
      return {
        ...segment,
        qc_drift: result.drift,
        qc_flagged: result.flagged,
        qc_recognized: result.recognized_text,
        qc_measured_start: result.measured_start ?? undefined,
        qc_measured_end: result.measured_end ?? undefined,
      };
    }),
  });
}
async function run(
  phase: DubSession['phase'],
  work: (signal: AbortSignal) => Promise<void>,
): Promise<boolean> {
  if (controller || cancelling) return false;
  const active = new AbortController();
  controller = active;
  patch({
    phase,
    error: null,
    failure: null,
    event: null,
    asrModelMissing: null,
  });
  const reportPhase =
    phase === 'preparing'
      ? 'prepare'
      : phase === 'translating'
        ? 'translate'
        : phase === 'generating'
          ? 'generate'
          : null;
  if (reportPhase) recordActionBreadcrumb(`dub:${reportPhase}:start`);
  try {
    await work(active.signal);
    if (reportPhase) recordActionBreadcrumb(`dub:${reportPhase}:complete`);
    return true;
  } catch (error) {
    if (reportPhase)
      recordActionBreadcrumb(`dub:${reportPhase}:${active.signal.aborted ? 'cancel' : 'error'}`);
    if (!active.signal.aborted) {
      const translationError = phase === 'translating' ? describeDubTranslationError(error) : null;
      if (error instanceof DubAsrModelMissingError)
        patch({ error: null, asrModelMissing: error.payload });
      else if (translationError) patch({ error: translationError });
      else
        patch({
          error: error instanceof Error ? error.message : String(error),
        });
    }
    const current = dubSession.state;
    // A lost stream does not mean the backend stopped. Keep the task recoverable
    // until its terminal event or confirmed cancellation, including before reload.
    const recovery =
      current.taskId && (current.phase === 'preparing' || current.phase === 'generating')
        ? current.phase
        : current.recovery;
    patch({ phase: current.segments.length ? 'editing' : 'idle', recovery });
    return false;
  } finally {
    if (controller === active) controller = null;
  }
}
function taskEvent(event: TaskEvent, terminal: string) {
  patch({ event });
  if (event.type === 'error' || event.type === 'cancelled') {
    patch({ taskId: null, recovery: null });
    if (event.type === 'cancelled') throw new DOMException('Cancelled', 'AbortError');
    if (event.error === 'asr_model_missing')
      throw new DubAsrModelMissingError(event as unknown as AsrModelMissing);
    const failure = publicFailureFromEvent(event, 'Task failed');
    patch({ failure });
    throw new Error(`${failure.stage ? failure.stage + ': ' : ''}${failure.reason}`);
  }
  return event.type === terminal;
}
async function transcribeJob(jobId: string, signal: AbortSignal) {
  const finishActivity = beginAppActivity('transcription');
  recordActionBreadcrumb('dub:transcribe:start');
  try {
    await readTranscript(jobId, signal);
    patch({ recovery: null });
    recordActionBreadcrumb('dub:transcribe:complete');
  } catch (error) {
    recordActionBreadcrumb(signal.aborted ? 'dub:transcribe:cancel' : 'dub:transcribe:error');
    // ASR is request-scoped rather than a replayable task. Explicit Retry uses
    // the already prepared media; never restart it automatically after reload.
    if (!signal.aborted) patch({ recovery: 'transcribing' });
    throw error;
  } finally {
    finishActivity();
  }
}
async function readTranscript(jobId: string, signal: AbortSignal) {
  patch({ phase: 'transcribing', taskId: null });
  let receivedTranscript = false;
  await consumeTaskStream(
    '/dub/transcribe-stream/' +
      encodeURIComponent(jobId) +
      (dubSession.state.numSpeakers ? '?num_speakers=' + dubSession.state.numSpeakers : ''),
    (event) => {
      if (event.type === 'final') {
        receivedTranscript = true;
        const raw = Array.isArray(event.segments) ? (event.segments as DubSegment[]) : [];
        const segments = raw.map((segment, index) => ({
          ...segment,
          id: String(segment.id ?? index),
          text_original: segment.text_original || segment.text || '',
        }));
        const clones = (event.cast_sources || event.speaker_clones || {}) as Record<
          string,
          unknown
        >;
        clearDubEditHistory();
        patch({
          segments: applySpeakerCloneDefaults(segments, clones),
          sourceLang: String(event.source_lang || ''),
        });
      }
      return taskEvent(event, 'done');
    },
    signal,
  );
  if (!receivedTranscript) throw new Error('Transcription ended without its result');
  patch({ phase: 'editing' });
}

async function useDownloadedCaptions(jobId: string, signal: AbortSignal) {
  const result = await apiJson<{
    segments: DubSegment[];
    source_lang?: string;
    caption_lang?: string;
  }>('/dub/use-downloaded-captions/' + encodeURIComponent(jobId), {
    method: 'POST',
    signal,
  });
  const segments = result.segments.map((segment, index) => ({
    ...segment,
    id: String(segment.id ?? index),
    text_original: segment.text_original || segment.text || '',
  }));
  clearDubEditHistory();
  patch({
    segments,
    sourceLang: String(result.source_lang || ''),
    phase: 'editing',
    taskId: null,
    recovery: null,
    event: { type: 'captions', language: result.caption_lang || '' },
  });
}

async function finishPreparation(jobId: string, taskId: string, signal: AbortSignal) {
  let hasDownloadedCaptions = false;
  await consumeTaskStream(
    '/tasks/stream/' + encodeURIComponent(taskId),
    (event) => {
      if (Number.isFinite(event.duration) && Number(event.duration) > 0)
        patch({ duration: Number(event.duration) });
      if (Array.isArray(event.youtube_subs) && event.youtube_subs.length > 0)
        hasDownloadedCaptions = true;
      return taskEvent(event, 'ready');
    },
    signal,
  );
  if (hasDownloadedCaptions) {
    try {
      await useDownloadedCaptions(jobId, signal);
      return;
    } catch (error) {
      if (signal.aborted) throw error;
      // A missing, malformed, or stale caption track must not strand the job.
      // The ordinary ASR path remains the transparent fallback.
    }
  }
  await transcribeJob(jobId, signal);
}

export async function uploadDub(file: File) {
  if (controller || cancelling || dubSession.state.recovery) return;
  recordActionBreadcrumb('dub:upload');
  clearDubEditHistory();
  const jobId = crypto.randomUUID();
  const inputType =
    file.type.startsWith('audio/') || /\.(wav|mp3|m4a|flac|ogg|aac)$/i.test(file.name)
      ? 'audio'
      : 'video';
  dubSession.setState((current) => ({
    ...initial,
    target: current.target,
    multiTargets: current.multiTargets,
    quality: current.quality,
    agentCli: current.agentCli,
    autoGlossary: current.autoGlossary,
    reflectPass: current.reflectPass,
    condenseSuggest: current.condenseSuggest,
    dialect: current.dialect,
    translationInstructions: current.translationInstructions,
    timingStrategy: current.timingStrategy,
    voiceMatch: current.voiceMatch,
    sourceLanguage: current.sourceLanguage,
    numSpeakers: current.numSpeakers,
    fitOptions: current.fitOptions,
    steps: current.steps,
    guidance: current.guidance,
    speed: current.speed,
    instruct: current.instruct,
    jobId,
    filename: file.name,
    inputType,
  }));
  await run('preparing', async (signal) => {
    const body = new FormData();
    body.set('video', file);
    body.set('job_id', jobId);
    body.set('input_type', inputType);
    if (dubSession.state.sourceLanguage) body.set('source_lang', dubSession.state.sourceLanguage);
    const upload = await apiJson<{ job_id: string; task_id: string }>('/dub/upload', {
      method: 'POST',
      body,
      signal,
    });
    await prepareAndTranscribe(upload, signal);
  });
}
async function prepareAndTranscribe(job: { job_id: string; task_id: string }, signal: AbortSignal) {
  patch({ jobId: job.job_id, taskId: job.task_id });
  await finishPreparation(job.job_id, job.task_id, signal);
}
export function isDubUrl(value: string) {
  try {
    return ['http:', 'https:'].includes(new URL(value.trim()).protocol);
  } catch {
    return false;
  }
}
export async function ingestDubUrl(value: string, cookieFile?: File, fetchSubs = false) {
  if (controller || cancelling || dubSession.state.recovery || !isDubUrl(value)) return;
  recordActionBreadcrumb('dub:ingest-url');
  clearDubEditHistory();
  const url = value.trim();
  const jobId = crypto.randomUUID();
  dubSession.setState((current) => ({
    ...initial,
    target: current.target,
    multiTargets: current.multiTargets,
    quality: current.quality,
    agentCli: current.agentCli,
    autoGlossary: current.autoGlossary,
    reflectPass: current.reflectPass,
    condenseSuggest: current.condenseSuggest,
    dialect: current.dialect,
    translationInstructions: current.translationInstructions,
    timingStrategy: current.timingStrategy,
    voiceMatch: current.voiceMatch,
    sourceLanguage: current.sourceLanguage,
    numSpeakers: current.numSpeakers,
    fitOptions: current.fitOptions,
    steps: current.steps,
    guidance: current.guidance,
    speed: current.speed,
    instruct: current.instruct,
    jobId,
    filename: url,
    inputType: 'video',
  }));
  await run('preparing', async (signal) => {
    if (
      cookieFile &&
      !(window.location.protocol === 'app:' && window.location.hostname === 'voicestudio') &&
      !_cookieTransportAllowed('/api')
    )
      throw new Error('DUB_COOKIE_TRANSPORT');
    if (cookieFile && cookieFile.size > MAX_COOKIE_EXPORT_BYTES)
      throw new Error('DUB_COOKIE_TOO_LARGE');
    const cookieText = cookieFile ? await cookieFile.text() : undefined;
    const job = await apiJson<{ job_id: string; task_id: string }>('/dub/ingest-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
      body: JSON.stringify({
        url,
        job_id: jobId,
        source_lang: dubSession.state.sourceLanguage,
        cookie_file: cookieText,
        fetch_subs: fetchSubs,
      }),
    });
    await prepareAndTranscribe(job, signal);
  });
}

async function runLocalTranslationAgent(
  request: DubAgentTranslationRequest,
  signal: AbortSignal,
  retry?: () => Promise<unknown>,
): Promise<DubAgentTranslationResult> {
  const bridge = window.voicestudio?.repair;
  if (!bridge?.translate) throw new Error('LOCAL_TRANSLATION_AGENT_UNAVAILABLE');
  const stop = () => void bridge.stopTranslation().catch(() => {});
  if (signal.aborted) {
    stop();
    throw new DOMException('Cancelled', 'AbortError');
  }
  const id = startTranslationRun({
    jobId: dubSession.state.jobId || '', agent: request.agent,
    target: request.targetLanguage, purpose: request.purpose, retry,
    rows: request.segments.map((segment) => ({ id: segment.id, source: segment.sourceText })),
  });
  const unsubscribe = bridge.onTranslationEvent?.((event) => {
    if (event.requestId === id) appendTranslationLog(id, event.text);
  });
  signal.addEventListener('abort', stop, { once: true });
  try {
    const result = await bridge.translate({ ...request, requestId: id });
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    const texts = new Map(result.translations.map((row) => [row.id, row.text]));
    updateTranslationRun(id, {
      rows: request.segments.map((segment) => ({ id: segment.id, source: segment.sourceText, text: texts.get(segment.id) })),
    });
    finishTranslationRun(id, 'complete');
    return result;
  } catch (error) {
    finishTranslationRun(id, signal.aborted ? 'cancelled' : 'failed',
      signal.aborted ? undefined : (error instanceof Error ? error.message : String(error)));
    throw error;
  } finally {
    unsubscribe?.();
    signal.removeEventListener('abort', stop);
  }
}

export async function translateDubWithAgent(
  target: string,
  agent: RepairAgentId,
  targetLabel = target,
): Promise<boolean> {
  const snapshot = dubSession.state;
  if (!snapshot.jobId || !snapshot.segments.length || snapshot.recovery) return false;
  const finishActivity = beginAppActivity('translation');
  try {
    return await run('translating', async (signal) => {
      const glossary = await apiJson<Array<{ source: string; target: string; note?: string }>>(
        `/glossary/${encodeURIComponent(snapshot.jobId!)}`,
        { signal },
      );
      const translated = await runLocalTranslationAgent(
        {
          agent,
          purpose: 'translate',
          sourceLanguage: snapshot.sourceLang || snapshot.sourceLanguage || undefined,
          targetLanguage: targetLabel,
          dialect: snapshot.dialect,
          translationInstructions: snapshot.translationInstructions,
          glossary,
          segments: snapshot.segments.map((segment) => ({
            id: segment.id,
            sourceText: segment.text_original || segment.text,
            start: segment.start,
            end: segment.end,
          })),
        },
        signal,
        () => translateDubWithAgent(target, agent, targetLabel),
      );
      const rows = new Map(translated.translations.map((row) => [row.id, row.text]));
      clearDubEditHistory();
      patch({
        quality: 'agent',
        agentCli: agent,
        translationFallback: false,
        segments: snapshot.segments.map((segment) => {
          const text = rows.get(segment.id);
          if (!text) return segment;
          const translateErrors = { ...segment.translate_errors };
          delete translateErrors[target];
          const agentLanguages = new Set(segment.agent_generated_langs || []);
          agentLanguages.add(target);
          return {
            ...withoutCueSource(segment),
            text,
            translations: { ...segment.translations, [target]: text },
            translate_error: undefined,
            translate_errors: Object.keys(translateErrors).length ? translateErrors : undefined,
            translate_degraded: undefined,
            translation_skipped: undefined,
            translate_literal: undefined,
            translate_critique: undefined,
            rate_error: undefined,
            plan: undefined,
            agent_generated_lang: target,
            agent_generated_langs: [...agentLanguages],
          };
        }),
        phase: 'editing',
      });
    });
  } finally {
    finishActivity();
  }
}

export async function translateDub(
  target: string,
  provider: string,
  options: { retryFailed?: boolean } = {},
): Promise<boolean> {
  const snapshot = dubSession.state;
  if (!snapshot.jobId || !snapshot.segments.length || snapshot.recovery) return false;
  const requestedSegments = options.retryFailed
    ? snapshot.segments.filter(
        (segment) =>
          segment.translate_errors?.[target] ||
          (!segment.translate_errors && segment.translate_error),
      )
    : snapshot.segments;
  if (!requestedSegments.length) return false;
  const finishActivity = beginAppActivity('translation');
  let agentFallback = false;
  let activityId: string | undefined;
  let activityAborted = false;
  try {
    const completed = await run('translating', async (signal) => {
      activityId = startTranslationRun({
        jobId: snapshot.jobId!, agent: provider, target, purpose: 'translate',
        rows: requestedSegments.map((segment) => ({ id: segment.id, source: segment.text_original || segment.text })),
        retry: () => translateDub(target, provider, { retryFailed: Boolean(dubSession.state.segments.some((s) => s.translate_errors?.[target])) }),
      });
      signal.addEventListener('abort', () => { activityAborted = true; }, { once: true });

      const glossary = await apiJson<Array<{ source: string; target: string; note?: string }>>(
        `/glossary/${encodeURIComponent(snapshot.jobId!)}`,
        { signal },
      );
      const translated = await apiJson<{
        cinematic_skipped?: string;
        dialect?: string;
        dialect_applied?: boolean;
        translated: Array<{
          id: string;
          text: string;
          error?: string;
          degraded?: string;
          literal?: string;
          critique?: string;
          rate_ratio?: number;
          rate_error?: string;
          plan?: DubSegment['plan'];
        }>;
      }>('/dub/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal,
        body: JSON.stringify({
          job_id: snapshot.jobId,
          source_lang: snapshot.sourceLang || undefined,
          target_lang: target,
          provider,
          quality: snapshot.quality,
          translation_instructions: snapshot.translationInstructions,
          auto_glossary: snapshot.autoGlossary ?? true,
          reflect: snapshot.reflectPass ?? true,
          condense: snapshot.condenseSuggest ?? false,
          dialect: snapshot.dialect
            ?.toLowerCase()
            .startsWith(`${target.toLowerCase().split('-')[0]}-`)
            ? snapshot.dialect
            : undefined,
          glossary: glossary.length
            ? glossary.map((term) => ({
                source: term.source,
                target: term.target,
                note: term.note || '',
              }))
            : undefined,
          segments: requestedSegments.map((segment) => ({
            id: segment.id,
            text: segment.text_original || segment.text,
            start: segment.start,
            end: segment.end,
            slot_seconds: segment.end - segment.start,
          })),
        }),
      });
      if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
      updateTranslationRun(activityId!, {
        rows: requestedSegments.map((segment) => {
          const row = translated.translated.find((r) => String(r.id) === segment.id);
          return { id: segment.id, source: segment.text_original || segment.text, text: row?.error ? undefined : row?.text, error: row?.error };
        }),
      });
      const fallback = translated.cinematic_skipped === 'no-llm-configured';
      agentFallback = fallback && snapshot.quality === 'agent';
      const rows = new Map(translated.translated.map((row) => [String(row.id), row]));
      clearDubEditHistory();
      patch({
        quality: fallback && !agentFallback ? 'fast' : snapshot.quality,
        translationFallback: fallback,
        segments: snapshot.segments.map((segment) => {
          const row = rows.get(segment.id);
          if (!row) return segment;
          const translatedText = row?.error ? segment.text : (row?.text ?? segment.text);
          const agentGenerated = !row?.error && snapshot.quality === 'agent' && !agentFallback;
          const agentLanguages = new Set(segment.agent_generated_langs || []);
          if (agentGenerated) agentLanguages.add(target);
          else agentLanguages.delete(target);
          const translateErrors = { ...segment.translate_errors };
          if (row?.error) translateErrors[target] = row.error;
          else delete translateErrors[target];
          return {
            ...(row?.error ? segment : withoutCueSource(segment)),
            text: translatedText,
            translations: {
              ...segment.translations,
              ...(row?.error ? {} : { [target]: translatedText }),
            },
            translate_error: row?.error,
            translate_errors: Object.keys(translateErrors).length ? translateErrors : undefined,
            translate_degraded: row?.degraded,
            translation_skipped: undefined,
            translate_literal: row?.literal,
            translate_critique: row?.critique,
            rate_ratio: row?.rate_ratio,
            rate_error: row?.rate_error,
            plan: row?.plan,
            agent_generated_lang: agentGenerated ? target : undefined,
            agent_generated_langs: agentLanguages.size ? [...agentLanguages] : undefined,
          };
        }),
        phase: 'editing',
      });
      if (translated.translated.some((row) => row.error))
        throw new Error('Some translation segments failed');
    });
    if (activityId) finishTranslationRun(activityId,
      activityAborted ? 'cancelled' : completed && !agentFallback ? 'complete' : 'failed',
      completed && !agentFallback ? undefined : dubSession.state.error || undefined);
    return completed && !agentFallback;
  } finally {
    finishActivity();
  }
}
async function watchGeneration(taskId: string, signal: AbortSignal) {
  let result: { syncScores: number[] } = { syncScores: [] };
  await consumeTaskStream(
    '/tasks/stream/' + encodeURIComponent(taskId),
    (event) => {
      if (event.type === 'done') {
        const syncScores = Array.isArray(event.sync_scores) ? event.sync_scores : [];
        result = {
          syncScores: syncScores.map((score) =>
            typeof score === 'number' && Number.isFinite(score) ? score : 1,
          ),
        };
        const fitStatus = Array.isArray(event.fit_status) ? event.fit_status : [];
        const languageCode =
          typeof event.language_code === 'string' && event.language_code
            ? event.language_code
            : undefined;
        const hashes =
          event.seg_hashes &&
          typeof event.seg_hashes === 'object' &&
          !Array.isArray(event.seg_hashes)
            ? Object.fromEntries(
                Object.entries(event.seg_hashes).filter(
                  (entry): entry is [string, string] => typeof entry[1] === 'string',
                ),
              )
            : null;
        patch({
          tracks: Array.isArray(event.tracks) ? (event.tracks as string[]) : [],
          generatedTiming: dubSession.state.pendingTiming || 'strict_slot',
          segments: dubSession.state.segments.map((segment, index) => ({
            ...segment,
            sync_ratio:
              typeof syncScores[index] === 'number' ? (syncScores[index] as number) : undefined,
            fit_status:
              fitStatus[index] && typeof fitStatus[index] === 'object'
                ? (fitStatus[index] as DubSegment['fit_status'])
                : undefined,
          })),
          ...(languageCode && hashes
            ? {
                fingerprintsByLang: {
                  ...dubSession.state.fingerprintsByLang,
                  [languageCode]: hashes,
                },
              }
            : {}),
        });
      }
      return taskEvent(event, 'done');
    },
    signal,
  );
  patch({ taskId: null });
  return result;
}
export async function generateDub(
  language: string,
  languageCode: string,
  options: { regenOnly?: string[]; preview?: boolean } = {},
): Promise<boolean> {
  const snapshot = dubSession.state;
  if (!snapshot.jobId || !snapshot.segments.length || snapshot.recovery) return false;
  const finishActivity = beginAppActivity('synthesis');
  try {
    return await run('generating', async (signal) => {
      let regenOnly = options.regenOnly;
      const agentEnabled = snapshot.quality === 'agent';
      const maxAgentPasses = 2;
      let agentPass = 0;

      while (true) {
        const current = dubSession.state;
        patch({
          phase: 'generating',
          event: null,
          agentPass: agentEnabled
            ? {
                stage: agentPass ? 'rerendering' : 'measuring',
                pass: agentPass + 1,
                remaining: regenOnly?.length || current.segments.length,
              }
            : undefined,
        });
        const job = await apiJson<{ task_id: string }>(
          '/dub/generate/' + encodeURIComponent(current.jobId!),
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal,
            body: JSON.stringify({
              segments: current.segments.map((segment) => ({
                start: segment.start,
                end: segment.end,
                gain: segment.gain !== undefined && segment.gain !== 1 ? segment.gain : undefined,
                ...segmentGenInputs(segment),
                // Keeps the imported caption markup for unchanged exports (#2295).
                cue_source_id: cueSourceId(segment),
              })),
              segment_ids: current.segments.map((segment) => segment.id),
              regen_only: regenOnly?.length ? regenOnly : null,
              language,
              language_code: languageCode,
              num_step: current.steps,
              guidance_scale: current.guidance ?? 2,
              speed: current.speed ?? 1,
              instruct: current.instruct || undefined,
              timing_strategy: current.timingStrategy || 'strict_slot',
              ...(current.timingStrategy === 'smart_fit' && current.fitOptions
                ? { fit_options: current.fitOptions }
                : {}),
              voice_match: current.voiceMatch || 'per_line',
              // Agent timing must measure the same render the user keeps.
              preview: agentEnabled ? false : (options.preview ?? false),
            }),
          },
        );
        patch({
          taskId: job.task_id,
          pendingTiming: current.timingStrategy || 'strict_slot',
        });
        const measured = await watchGeneration(job.task_id, signal);
        if (!agentEnabled || agentPass >= maxAgentPasses) break;

        const afterRender = dubSession.state;
        const misses = afterRender.segments.flatMap((segment, index) => {
          const ratio = measured.syncScores[index];
          if (
            segment.agent_generated_lang !== languageCode ||
            !Number.isFinite(ratio) ||
            (ratio >= 0.9 && ratio <= 1.04) ||
            ratio < 0.45
          )
            return [];
          return [
            {
              id: segment.id,
              text: segment.text,
              source_text: segment.text_original,
              context_before: afterRender.segments[index - 1]?.text_original,
              context_after: afterRender.segments[index + 1]?.text_original,
              slot_seconds: segment.end - segment.start,
              measured_seconds: ratio * (segment.end - segment.start),
            },
          ];
        });
        if (!misses.length) break;

        patch({
          phase: 'translating',
          event: null,
          agentPass: {
            stage: 'adapting',
            pass: agentPass + 1,
            remaining: misses.length,
          },
        });
        const fitted = current.agentCli
          ? await runLocalTranslationAgent(
              {
                agent: current.agentCli,
                purpose: 'fit',
                sourceLanguage: current.sourceLang || current.sourceLanguage || undefined,
                targetLanguage: language,
                dialect: current.dialect,
                translationInstructions: current.translationInstructions,
                segments: misses.map((segment) => ({
                  id: segment.id,
                  sourceText: segment.source_text || segment.text,
                  currentText: segment.text,
                  start: 0,
                  end: segment.slot_seconds,
                  measuredSeconds: segment.measured_seconds,
                })),
              },
              signal,
            ).then((result) => ({
              segments: result.translations.map((row) => ({
                ...row,
                changed: row.text !== misses.find((segment) => segment.id === row.id)?.text,
                error: undefined as string | undefined,
              })),
            }))
          : await apiJson<{
              segments: Array<{ id: string; text: string; changed: boolean; error?: string }>;
            }>('/dub/agent-fit', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              signal,
              body: JSON.stringify({ target_lang: languageCode, segments: misses, translation_instructions: current.translationInstructions }),
            });
        if (fitted.segments.some((row) => AGENT_FIT_BLOCKING_ERRORS.has(row.error || '')))
          throw new Error(DUB_AGENT_UNAVAILABLE);
        const changed = new Map(
          fitted.segments
            .filter((row) => row.changed && row.text.trim())
            .map((row) => [String(row.id), row.text.trim()]),
        );
        if (!changed.size) break;
        patch({
          segments: dubSession.state.segments.map((segment) => {
            const text = changed.get(segment.id);
            return text
              ? {
                  ...withoutCueSource(segment),
                  text,
                  translations: { ...segment.translations, [languageCode]: text },
                  sync_ratio: undefined,
                  fit_status: undefined,
                }
              : segment;
          }),
        });
        regenOnly = [...changed.keys()];
        agentPass += 1;
      }
      patch({ phase: 'done', taskId: null, agentPass: undefined });
    });
  } finally {
    patch({ agentPass: undefined });
    finishActivity();
  }
}

export async function planDubIncremental(
  languageCode: string,
  signal?: AbortSignal,
): Promise<{ stale: string[]; fresh: string[] } | null> {
  const snapshot = dubSession.state;
  const stored = snapshot.fingerprintsByLang?.[languageCode];
  if (!snapshot.segments.length || !stored || !Object.keys(stored).length) return null;
  const result = await apiJson<{ stale?: unknown; fresh?: unknown }>('/tools/incremental', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      segments: snapshot.segments.map((segment) => ({
        id: String(segment.id),
        ...segmentGenInputs(segment),
      })),
      stored_hashes: stored,
      lang: languageCode,
      voice_match: snapshot.voiceMatch || 'per_line',
    }),
  });
  const knownIds = new Set(snapshot.segments.map((segment) => segment.id));
  return {
    stale: Array.isArray(result.stale)
      ? result.stale.map(String).filter((id) => knownIds.has(id))
      : [],
    fresh: Array.isArray(result.fresh)
      ? result.fresh.map(String).filter((id) => knownIds.has(id))
      : [],
  };
}

export async function generateDubBatch(
  targets: Array<{ lang: string; code: string }>,
  provider?: string,
): Promise<string[]> {
  const unique = targets.filter(
    (target, index, all) =>
      target.code && all.findIndex((candidate) => candidate.code === target.code) === index,
  );
  if (!unique.length || controller || cancelling || dubSession.state.recovery) return [];
  const runId = ++batchRunId;
  const skipped: string[] = [];
  for (const [index, target] of unique.entries()) {
    if (runId !== batchRunId || dubSession.state.recovery) break;
    patch({
      batchProgress: {
        current: index + 1,
        total: unique.length,
        language: target.lang,
      },
    });
    let translated = true;
    if (
      (dubSession.state.quality !== 'agent' || Boolean(dubSession.state.agentCli)) &&
      hasCompleteTranslation(dubSession.state.segments, target.code)
    ) {
      patch({
        target: target.lang,
        segments: dubSession.state.segments.map((segment) => ({
          ...segment,
          text: segment.translations?.[target.code] || segment.text,
          agent_generated_lang: segment.agent_generated_langs?.includes(target.code)
            ? target.code
            : undefined,
        })),
      });
    } else translated = provider ? await translateDub(target.code, provider) : false;
    if (!translated) {
      if (runId === batchRunId) skipped.push(target.lang);
      continue;
    }
    if (runId !== batchRunId) break;
    const generated = await generateDub(target.lang, target.code);
    if (!generated && runId === batchRunId) skipped.push(target.lang);
  }
  if (runId === batchRunId) {
    const primary = unique[0];
    patch({
      target: primary.lang,
      phase: dubSession.state.tracks.length ? 'done' : 'editing',
      segments: dubSession.state.segments.map((segment) => ({
        ...segment,
        text: segment.translations?.[primary.code] || segment.text_original || segment.text,
        agent_generated_lang: segment.agent_generated_langs?.includes(primary.code)
          ? primary.code
          : undefined,
      })),
      batchProgress: undefined,
    });
  }
  return skipped;
}

export async function translateDubBatch(
  targets: Array<{ lang: string; code: string }>,
  provider: string,
): Promise<string[]> {
  const unique = targets.filter(
    (target, index, all) =>
      target.code && all.findIndex((candidate) => candidate.code === target.code) === index,
  );
  if (!unique.length || controller || cancelling || dubSession.state.recovery) return [];
  const runId = ++batchRunId;
  const skipped: string[] = [];
  for (const [index, target] of unique.entries()) {
    if (runId !== batchRunId || dubSession.state.recovery) break;
    patch({
      batchProgress: {
        current: index + 1,
        total: unique.length,
        language: target.lang,
      },
    });
    if (!(await translateDub(target.code, provider)) && runId === batchRunId)
      skipped.push(target.lang);
  }
  if (runId === batchRunId) {
    const primary = unique[0];
    patch({
      target: primary.lang,
      phase: 'editing',
      segments: dubSession.state.segments.map((segment) => ({
        ...segment,
        text: segment.translations?.[primary.code] || segment.text_original || segment.text,
        agent_generated_lang: segment.agent_generated_langs?.includes(primary.code)
          ? primary.code
          : undefined,
      })),
      batchProgress: undefined,
    });
  }
  return skipped;
}

export async function translateDubBatchWithAgent(
  targets: Array<{ lang: string; code: string }>,
  agent: RepairAgentId,
): Promise<string[]> {
  const unique = targets.filter(
    (target, index, all) =>
      target.code && all.findIndex((candidate) => candidate.code === target.code) === index,
  );
  if (!unique.length || controller || cancelling || dubSession.state.recovery) return [];
  const runId = ++batchRunId;
  const skipped: string[] = [];
  for (const [index, target] of unique.entries()) {
    if (runId !== batchRunId || dubSession.state.recovery) break;
    patch({
      batchProgress: { current: index + 1, total: unique.length, language: target.lang },
    });
    if (!(await translateDubWithAgent(target.code, agent, target.lang)) && runId === batchRunId)
      skipped.push(target.lang);
  }
  if (runId === batchRunId) {
    const primary = unique[0];
    patch({
      target: primary.lang,
      phase: 'editing',
      segments: dubSession.state.segments.map((segment) => ({
        ...segment,
        text: segment.translations?.[primary.code] || segment.text_original || segment.text,
        agent_generated_lang: segment.agent_generated_langs?.includes(primary.code)
          ? primary.code
          : undefined,
      })),
      batchProgress: undefined,
    });
  }
  return skipped;
}
export async function cancelDub() {
  if (cancelling) return;
  const { jobId, taskId, phase, batchProgress } = dubSession.state;
  const recovery =
    dubSession.state.recovery ||
    (phase === 'transcribing'
      ? 'transcribing'
      : taskId && (phase === 'preparing' || phase === 'generating')
        ? phase
        : null);
  if (!controller && !recovery && !batchProgress) return;
  batchRunId += 1;
  patch({ batchProgress: undefined });
  cancelling = true;
  dubCancelling.setState(() => true);
  controller?.abort();
  try {
    const results = await Promise.allSettled([
      ...(jobId
        ? [
            apiJson('/dub/abort/' + encodeURIComponent(jobId), {
              method: 'POST',
            }),
          ]
        : []),
      ...(taskId
        ? [
            apiJson('/tasks/cancel/' + encodeURIComponent(taskId), {
              method: 'POST',
            }),
          ]
        : []),
    ]);
    if (
      results.every(
        (result) =>
          result.status === 'fulfilled' ||
          (result.reason instanceof ApiError && result.reason.status === 404),
      )
    )
      patch({ recovery: null, taskId: null });
    else patch({ recovery, error: DUB_STOP_FAILED });
  } finally {
    cancelling = false;
    dubCancelling.setState(() => false);
  }
}

export function dismissDubError(): void {
  patch({ error: null, failure: null, asrModelMissing: null });
}

/** Leave an unreachable recovered task locally so a new source can be started. */
export function discardDubRecovery(): void {
  if (controller || cancelling || !dubSession.state.recovery) return;
  const current = dubSession.state;
  clearDubEditHistory();
  dubSession.setState(() => ({
    ...initial,
    target: current.target,
    multiTargets: current.multiTargets,
    quality: current.quality,
    agentCli: current.agentCli,
    autoGlossary: current.autoGlossary,
    reflectPass: current.reflectPass,
    condenseSuggest: current.condenseSuggest,
    dialect: current.dialect,
    translationInstructions: current.translationInstructions,
    timingStrategy: current.timingStrategy,
    voiceMatch: current.voiceMatch,
    sourceLanguage: current.sourceLanguage,
    numSpeakers: current.numSpeakers,
    fitOptions: current.fitOptions,
    steps: current.steps,
    guidance: current.guidance,
    speed: current.speed,
    instruct: current.instruct,
    exportOptions: current.exportOptions,
  }));
  persist();
}

/** Drop the current source so a new video or URL can be started. Production
 * preferences (target, quality, voice, timing, …) are kept; everything
 * source-specific (job, segments, transcript, errors) is cleared. */
export function resetDubSession(): boolean {
  if (controller || cancelling) return false;
  const current = dubSession.state;
  clearDubEditHistory();
  dubSession.setState(() => ({
    ...initial,
    target: current.target,
    multiTargets: current.multiTargets,
    quality: current.quality,
    agentCli: current.agentCli,
    autoGlossary: current.autoGlossary,
    reflectPass: current.reflectPass,
    condenseSuggest: current.condenseSuggest,
    dialect: current.dialect,
    translationInstructions: current.translationInstructions,
    timingStrategy: current.timingStrategy,
    voiceMatch: current.voiceMatch,
    sourceLanguage: current.sourceLanguage,
    numSpeakers: current.numSpeakers,
    fitOptions: current.fitOptions,
    steps: current.steps,
    guidance: current.guidance,
    speed: current.speed,
    instruct: current.instruct,
    exportOptions: current.exportOptions,
  }));
  persist();
  return true;
}

export async function resumeDub() {
  const { recovery, taskId, jobId } = dubSession.state;
  if (!recovery || !jobId || (recovery !== 'transcribing' && !taskId)) return;
  if (recovery === 'transcribing') {
    await run('transcribing', (signal) => transcribeJob(jobId, signal));
    return;
  }
  if (!taskId) return;
  await run(recovery, async (signal) => {
    // Read and replay the existing persisted task. Never POST a replacement generation.
    try {
      await apiJson('/jobs/' + encodeURIComponent(taskId), { signal });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404)
        patch({ recovery: null, taskId: null });
      throw error;
    }
    if (recovery === 'generating') {
      await watchGeneration(taskId, signal);
      patch({ phase: 'done' });
    } else await finishPreparation(jobId, taskId, signal);
    patch({ recovery: null });
  });
}

export async function importDubSubtitles(file: File) {
  const snapshot = dubSession.state;
  if (!snapshot.jobId || snapshot.recovery || controller || cancelling) return;
  await run('importing', async (signal) => {
    const body = new FormData();
    body.set('file', file);
    const result = await apiJson<{
      segments: DubSegment[];
      stats: Record<string, number>;
    }>('/dub/import-srt/' + encodeURIComponent(snapshot.jobId!), {
      method: 'POST',
      body,
      signal,
    });
    if (signal.aborted) return;
    // The backend carries references by time overlap. Never reassign by cue index.
    clearDubEditHistory();
    patch({
      segments: result.segments.map((segment, index) => ({
        ...segment,
        id: String(segment.id ?? index),
        text_original: segment.text_original ?? segment.text,
      })),
      phase: 'editing',
      tracks: [],
      subtitleImport: { file: file.name, stats: result.stats },
    });
  });
}

export async function cleanupDubSegments(): Promise<number | null> {
  const snapshot = dubSession.state;
  if (!snapshot.jobId || !snapshot.segments.length || !editingAllowed()) return null;
  let removed: number | null = null;
  const returnPhase: DubSession['phase'] = snapshot.tracks.length ? 'done' : 'editing';
  const completed = await run('cleaning', async (signal) => {
    const result = await apiJson<{
      segments: DubSegment[];
      before: number;
      after: number;
    }>('/dub/cleanup-segments/' + encodeURIComponent(snapshot.jobId!), {
      method: 'POST',
      signal,
    });
    if (signal.aborted || dubSession.state.jobId !== snapshot.jobId) return;
    const next = result.segments.map((segment, index) => ({
      ...segment,
      id: String(segment.id ?? index),
      text_original:
        typeof segment.text_original === 'string' ? segment.text_original : segment.text,
    }));
    patch({ phase: returnPhase });
    commitSegmentEdit(next);
    removed = Math.max(0, result.before - result.after);
  });
  return completed ? removed : null;
}

export function openDubProject(project: DubProject): boolean {
  if (controller || cancelling || dubSession.state.recovery) return false;
  clearDubEditHistory();
  dubSession.setState(() => projectSession(project, initial));
  persist();
  return true;
}
export function attachDubProject(snapshot: DubSession, project: DubProject) {
  if (dubSession.state === snapshot) {
    patch({ project });
    persist();
  }
}

export function detachDubProject(id: string) {
  if (dubSession.state.project?.id === id) {
    patch({ project: undefined });
    persist();
  }
}
