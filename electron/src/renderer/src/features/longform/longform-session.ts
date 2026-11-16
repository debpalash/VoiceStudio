import {
  DEFAULT_OVERRIDES,
  overridesToRequest,
  type Overrides,
} from '@shared/utils/longformOverrides';
import { castVoice } from './cast-map';
import { parseCastNames } from '@shared/utils/audiobookScript';
import { restoreBookOptions, lexiconMap, type BookOptions } from './book-options';
import { createCoalescedJsonStorage } from '@shared/utils/coalescedJsonStorage';
import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import { absoluteApiBase, apiFetch } from '@/lib/api/client';
import { ADMIN_SESSION_STORAGE_KEY } from '@shared/api/authSession';
import {
  getBackendStatusSnapshot,
  isBackendReachable,
  subscribeBackendStatus,
} from '@/hooks/use-backend-status';
import { queryClient } from '@/lib/query';
import { cachedTtsLanguagesSupported } from '@/lib/language-options';
import { tr } from '@/lib/i18n-text';
import { consumeLongformStream } from '@shared/utils/longformStream';
import { storyToSpans } from '@shared/utils/storyToSpans';
import { beginAppActivity } from '@/lib/app-activity';
import { publishLongformActivity } from '@/lib/longform-activity';
import { publicFailureFromEvent, type PublicFailure } from '@/lib/api/failure';
import { parseProgressEvent, type RenderProgressSnapshot } from './render-eta';
export type Mode = 'stories' | 'audiobook';
export interface Character {
  id: string;
  name: string;
  profileId: string | null;
}
export interface Line {
  character?: string;
  speed?: number | null;
  id: string;
  text: string;
  profileId: string | null;
}
export interface AudiobookRenderChapter {
  title: string;
  status: string;
  duration_s?: number;
  duration_ms?: number;
  error?: string;
}
export interface Draft extends BookOptions {
  importText: string;
  cast: Character[];
  globalSpeed: number;
  projectId: string | null;
  overrides: Overrides;
  voiceCast: Record<string, string>;
  script: string;
  lines: Line[];
  title: string;
  voice: string | null;
  format: 'mp3' | 'm4b';
  language: string;
  output: string;
  /** Opaque local provenance only; endpoint and storage identities stay in private session state. */
  outputOwner: string | null;
  outputScript: string;
  outputChapters: AudiobookRenderChapter[];
  outputCachedChapters: number;
  outputFailedChapters: number;
}
interface Session {
  drafts: Record<Mode, Draft>;
  active: Mode | null;
  stage: string;
  completed: number;
  total: number;
  failed: number;
  error: string | null;
  failure: PublicFailure | null;
  storageError: boolean;
  chapters: AudiobookRenderChapter[];
  stopped: boolean;
  /** Latest per-chunk `progress` event (#3417); null on older backends. */
  progress: RenderProgressSnapshot | null;
  /** `Date.now()` of `started`, kept here so leaving the page keeps the clock. */
  startedAt: number | null;
  /** `Date.now()` of the last sign of life — the stall detector's baseline. */
  lastActivityAt: number | null;
}
export const blankLongformDraft = (): Draft => ({
  projectId: null,
  importText: '',
  cast: [],
  globalSpeed: 1,
  ...restoreBookOptions(null),
  overrides: { ...DEFAULT_OVERRIDES },
  voiceCast: {},
  script: '',
  lines: [],
  title: '',
  voice: null,
  format: 'm4b',
  language: 'Auto',
  output: '',
  outputOwner: null,
  outputScript: '',
  outputChapters: [],
  outputCachedChapters: 0,
  outputFailedChapters: 0,
});
const key = 'voicestudio.longform.v1';
const resultFields = [
  'output',
  'outputOwner',
  'outputScript',
  'outputChapters',
  'outputCachedChapters',
  'outputFailedChapters',
] as const;
type CompletedResult = Pick<Draft, (typeof resultFields)[number]> & { availabilityOnly?: boolean };
export interface LongformResultState {
  status: 'available' | 'other-backend' | 'legacy' | 'unknown' | 'none';
  recovering: boolean;
  recoveryFailed: boolean;
  availabilityOnly: boolean;
}
const emptyResult = (): CompletedResult => ({
  output: '',
  outputOwner: null,
  outputScript: '',
  outputChapters: [],
  outputCachedChapters: 0,
  outputFailedChapters: 0,
});
const ownerRecords = new Map<string, string>();
const resultSlots: Record<Mode, Map<string, CompletedResult>> = {
  stories: new Map(),
  audiobook: new Map(),
};
const legacyResults: Record<Mode, CompletedResult | null> = { stories: null, audiobook: null };
const draftGeneration: Record<Mode, number> = { stories: 0, audiobook: 0 };
const latestRender: Record<Mode, Map<string | null, number>> = {
  stories: new Map(),
  audiobook: new Map(),
};
let renderSequence = 0;
const recovery: Record<Mode, { recovering: boolean; recoveryFailed: boolean; revision: number }> = {
  stories: { recovering: false, recoveryFailed: false, revision: 0 },
  audiobook: { recovering: false, recoveryFailed: false, revision: 0 },
};
const resultStates: Record<Mode, LongformResultState> = {
  stories: { status: 'none', recovering: false, recoveryFailed: false, availabilityOnly: false },
  audiobook: { status: 'none', recovering: false, recoveryFailed: false, availabilityOnly: false },
};
const recoveryControllers: Record<Mode, AbortController | null> = {
  stories: null,
  audiobook: null,
};
let authorizationBlocked = false;
let persistenceClosed = false;
let connectionRevision = 0;
let connectionIdentity = '';
let currentOwner: string | null = null;
const opaqueOwner = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
function restoredResult(value: unknown): CompletedResult | null {
  if (!value || typeof value !== 'object') return null;
  const s = value as Partial<Draft> & { availabilityOnly?: unknown };
  if (typeof s.output !== 'string' || !s.output) return null;
  return {
    output: s.output,
    outputOwner: opaqueOwner(s.outputOwner) ? s.outputOwner : null,
    availabilityOnly: s.availabilityOnly === true,
    outputScript: typeof s.outputScript === 'string' ? s.outputScript : '',
    outputChapters: Array.isArray(s.outputChapters)
      ? s.outputChapters.filter(
          (chapter) =>
            chapter && typeof chapter.title === 'string' && typeof chapter.status === 'string',
        )
      : [],
    outputCachedChapters: typeof s.outputCachedChapters === 'number' ? s.outputCachedChapters : 0,
    outputFailedChapters: typeof s.outputFailedChapters === 'number' ? s.outputFailedChapters : 0,
  };
}
function retainResult(mode: Mode, value: unknown): void {
  const result = restoredResult(value);
  if (!result) return;
  if (result.outputOwner && [...ownerRecords.values()].includes(result.outputOwner)) {
    const previous = resultSlots[mode].get(result.outputOwner);
    // The public draft intentionally omits this private warning flag. Its
    // duplicate must not upgrade an availability-only check during hydration.
    if (previous?.output === result.output && previous.availabilityOnly)
      result.availabilityOnly = true;
    resultSlots[mode].set(result.outputOwner, result);
  } else if (!legacyResults[mode] || legacyResults[mode]?.output !== result.output) {
    // Hydration also sees the public draft. Keep the original private legacy
    // evidence when that duplicate came from an availability-only check.
    legacyResults[mode] = result;
  }
}
const drafts = {
  stories: blankLongformDraft(),
  audiobook: blankLongformDraft(),
};
try {
  const saved = JSON.parse(localStorage.getItem(key) || 'null');
  const privateResults = saved?.__resultOwnership;
  if (privateResults?.version === 1) {
    if (Array.isArray(privateResults.owners))
      for (const pair of privateResults.owners) {
        if (
          Array.isArray(pair) &&
          pair.length === 2 &&
          typeof pair[0] === 'string' &&
          opaqueOwner(pair[1])
        )
          ownerRecords.set(pair[0], pair[1]);
      }
    for (const mode of ['stories', 'audiobook'] as const) {
      const results = privateResults[mode];
      if (Array.isArray(results?.owned))
        for (const result of results.owned) retainResult(mode, result);
      const legacy = restoredResult(results?.legacy);
      if (legacy) legacyResults[mode] = legacy;
    }
  }
  for (const mode of ['stories', 'audiobook'] as const) {
    const s = saved?.[mode];
    if (s && typeof s.script === 'string' && Array.isArray(s.lines))
      drafts[mode] = {
        ...restoreBookOptions(s),
        importText: typeof s.importText === 'string' ? s.importText : '',
        cast: Array.isArray(s.cast)
          ? s.cast.filter(
              (c: Character) => c && typeof c.id === 'string' && typeof c.name === 'string',
            )
          : [],
        globalSpeed:
          typeof s.globalSpeed === 'number' && s.globalSpeed >= 0.5 && s.globalSpeed <= 2
            ? s.globalSpeed
            : 1,
        projectId: typeof s.projectId === 'string' ? s.projectId : null,
        overrides: { ...DEFAULT_OVERRIDES, ...s.overrides },
        voiceCast: Object.fromEntries(
          Object.entries(s.voiceCast || {}).flatMap(([key, value]) =>
            typeof value === 'string' ? [[key, value]] : [],
          ),
        ),
        script: s.script,
        lines: s.lines.filter(
          (line: Line) => line && typeof line.id === 'string' && typeof line.text === 'string',
        ),
        title: typeof s.title === 'string' ? s.title : '',
        voice: typeof s.voice === 'string' ? s.voice : null,
        format: s.format === 'mp3' ? 'mp3' : 'm4b',
        language: typeof s.language === 'string' ? s.language : 'Auto',
        ...emptyResult(),
      };
    retainResult(mode, s);
  }
} catch {
  /* Invalid drafts do not prevent opening the editor. */
}
export const longformSession = new Store<Session>({
  drafts,
  active: null,
  stage: '',
  completed: 0,
  total: 0,
  failed: 0,
  error: null,
  failure: null,
  storageError: false,
  chapters: [],
  stopped: false,
  progress: null,
  startedAt: null,
  lastActivityAt: null,
});
export const useLongformSession = () => useStore(longformSession);
/** Fences document imports that finish after a new dub replaces the Stories draft. */
export const storiesImportEpoch = { current: 0 };
const patch = (value: Partial<Session>) => longformSession.setState((s) => ({ ...s, ...value }));
const optionalFiniteNumber = (value: unknown): number | undefined => {
  if (value === null || value === undefined) return undefined;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : undefined;
};
/** Mirror the live render into the app-wide activity store (notifications, other page). */
function publishActivity() {
  const s = longformSession.state;
  publishLongformActivity(
    s.active
      ? {
          mode: s.active,
          title: s.drafts[s.active].title,
          stage: s.stage,
          completed: s.completed,
          total: s.total,
          percent: Math.round(
            s.progress ? s.progress.fraction * 100 : s.total ? (s.completed / s.total) * 100 : 0,
          ),
        }
      : null,
    s.active ? stopLongform : null,
  );
}
const unsubscribeActivity = longformSession.subscribe(publishActivity);
export function editLongform(mode: Mode, value: Partial<Draft>) {
  if (longformSession.state.active || persistenceClosed) return;
  // An explicit output reset is a lifecycle boundary, not a backend switch.
  if ('output' in value) {
    invalidateResults(mode);
    retainResult(mode, { ...longformSession.state.drafts[mode], ...value });
  }
  updateDraft(mode, value);
  if ('output' in value) projectResult(mode);
}
/** New/Open/dub replacement must never inherit a previous draft's backend slots. */
export function replaceLongformDraft(mode: Mode, value: Draft): void {
  if (longformSession.state.active || persistenceClosed) return;
  invalidateResults(mode);
  retainResult(mode, value);
  updateDraft(mode, { ...value, ...emptyResult() });
  projectResult(mode);
}
function invalidateResults(mode: Mode): void {
  draftGeneration[mode]++;
  latestRender[mode].clear();
  resultSlots[mode].clear();
  legacyResults[mode] = null;
  cancelRecovery(mode);
}
function cancelRecovery(mode: Mode): void {
  recoveryControllers[mode]?.abort();
  recoveryControllers[mode] = null;
  recovery[mode] = {
    recovering: false,
    recoveryFailed: false,
    revision: recovery[mode].revision + 1,
  };
}
function normalizedEndpoint(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      return null;
    return url.toString().replace(/\/+$/, '');
  } catch {
    return null;
  }
}
function backendIdentity(): { owner: string | null; connection: string } {
  if (typeof window === 'undefined' || authorizationBlocked) return { owner: null, connection: '' };
  if (window.voicestudio?.backend) {
    const status = getBackendStatusSnapshot();
    const endpoint = normalizedEndpoint(status.baseUrl);
    if (!isBackendReachable(status.stage) || !endpoint) return { owner: null, connection: '' };
    const owner = status.remote
      ? `remote:${endpoint}`
      : JSON.stringify(['local', status.runtimeStorage?.data ?? null]);
    return { owner, connection: JSON.stringify([owner, endpoint]) };
  }
  const endpoint = normalizedEndpoint(absoluteApiBase());
  return { owner: endpoint ? `browser:${endpoint}` : null, connection: endpoint ?? '' };
}
function synchronizeBackend(): void {
  if (persistenceClosed) return;
  const identity = backendIdentity();
  if (identity.connection !== connectionIdentity) {
    connectionIdentity = identity.connection;
    connectionRevision++;
    for (const mode of ['stories', 'audiobook'] as const) {
      cancelRecovery(mode);
    }
  }
  currentOwner = identity.owner ? (ownerRecords.get(identity.owner) ?? null) : null;
  if (identity.owner && !currentOwner && !persistenceClosed) {
    currentOwner = crypto.randomUUID();
    ownerRecords.set(identity.owner, currentOwner);
  }
  for (const mode of ['stories', 'audiobook'] as const) projectResult(mode);
}
function projectResult(mode: Mode): void {
  const result = currentOwner ? resultSlots[mode].get(currentOwner) : null;
  const previous = resultStates[mode];
  const state: LongformResultState = {
    status: !currentOwner
      ? legacyResults[mode] || resultSlots[mode].size
        ? 'unknown'
        : 'none'
      : result
        ? 'available'
        : legacyResults[mode]
          ? 'legacy'
          : resultSlots[mode].size
            ? 'other-backend'
            : 'none',
    recovering: recovery[mode].recovering,
    recoveryFailed: recovery[mode].recoveryFailed,
    availabilityOnly: result?.availabilityOnly === true,
  };
  const stateChanged =
    state.status !== previous.status ||
    state.recovering !== previous.recovering ||
    state.recoveryFailed !== previous.recoveryFailed ||
    state.availabilityOnly !== previous.availabilityOnly;
  resultStates[mode] = stateChanged ? state : previous;
  const draft = longformSession.state.drafts[mode];
  // A filename check proves availability, not render ownership. Named projects
  // must require another explicit check rather than promote that weak evidence.
  const projectedOwner = result?.availabilityOnly ? null : (result?.outputOwner ?? null);
  const changed = result
    ? resultFields.some(
        (key) => draft[key] !== (key === 'outputOwner' ? projectedOwner : result[key]),
      )
    : Boolean(
        draft.output ||
        draft.outputOwner ||
        draft.outputScript ||
        draft.outputChapters.length ||
        draft.outputCachedChapters ||
        draft.outputFailedChapters,
      );
  if (changed) {
    // Export only proven opaque ownership: endpoint/cache metadata stays private.
    const projected = result
      ? {
          ...Object.fromEntries(resultFields.map((key) => [key, result[key]])),
          outputOwner: projectedOwner,
        }
      : emptyResult();
    updateDraft(mode, projected);
  } else if (stateChanged) patch({});
}
export function getLongformResultState(mode: Mode): LongformResultState {
  return resultStates[mode];
}
/** User-requested availability check; never contacts a saved backend or claims byte identity. */
export async function recoverLegacyLongformResult(mode: Mode): Promise<boolean> {
  const result = legacyResults[mode];
  if (
    !result ||
    !currentOwner ||
    persistenceClosed ||
    recovery[mode].recovering ||
    longformSession.state.active
  )
    return false;
  const owner = currentOwner;
  const generation = draftGeneration[mode];
  const connection = connectionRevision;
  const attempt = ++recovery[mode].revision;
  const request = new AbortController();
  recoveryControllers[mode] = request;
  let succeeded = false;
  recovery[mode].recovering = true;
  recovery[mode].recoveryFailed = false;
  projectResult(mode);
  const stillCurrent = () =>
    !persistenceClosed &&
    draftGeneration[mode] === generation &&
    connectionRevision === connection &&
    currentOwner === owner &&
    recovery[mode].revision === attempt;
  try {
    const response = await apiFetch('/audio/' + encodeURIComponent(result.output), {
      method: 'HEAD',
      signal: request.signal,
    });
    if (!response.ok || !stillCurrent()) return false;
    resultSlots[mode].set(owner, { ...result, outputOwner: owner, availabilityOnly: true });
    // Availability is not original-byte identity. Keep the original snapshot.
    succeeded = true;
    queuePersistence();
    return true;
  } catch {
    return false;
  } finally {
    if (stillCurrent()) {
      recovery[mode].recovering = false;
      recoveryControllers[mode] = null;
      recovery[mode].recoveryFailed = !succeeded;
      projectResult(mode);
    }
  }
}
const storage = createCoalescedJsonStorage({
  warn: () => patch({ storageError: true }),
});
storage.configurePersistenceRole('main');
export function flushLongformSessionPersistence(): void {
  storage.flushPendingWrites();
}
const removeLifecycle = storage.installPersistenceLifecycleFlush();
if (import.meta.hot)
  import.meta.hot.dispose(() => {
    storage.flushPendingWrites();
    removeLifecycle();
    unsubscribeActivity.unsubscribe();
    unsubscribeBackend();
    window.removeEventListener('ov:auth-required', authRequired);
    window.removeEventListener('storage', authStorageChanged);
    for (const mode of ['stories', 'audiobook'] as const) cancelRecovery(mode);
    controller?.abort();
  });
function updateDraft(mode: Mode, value: Partial<Draft>) {
  const next = {
    ...longformSession.state.drafts,
    [mode]: { ...longformSession.state.drafts[mode], ...value },
  };
  patch({ drafts: next });
  queuePersistence();
}
function queuePersistence(): void {
  if (!persistenceClosed)
    storage.queueJsonWrite(key, () => ({
      ...longformSession.state.drafts,
      __resultOwnership: {
        version: 1,
        owners: [...ownerRecords],
        ...Object.fromEntries(
          (['stories', 'audiobook'] as const).map((mode) => [
            mode,
            { owned: [...resultSlots[mode].values()], legacy: legacyResults[mode] },
          ]),
        ),
      },
    }));
}

/** Prevent queued and pagehide writes from recreating a draft during a confirmed data reset. */
function authRequired(): void {
  authorizationBlocked = true;
  synchronizeBackend();
}
function authStorageChanged(event: StorageEvent): void {
  if (event.key !== null && event.key !== ADMIN_SESSION_STORAGE_KEY) return;
  connectionRevision++;
  for (const mode of ['stories', 'audiobook'] as const) {
    cancelRecovery(mode);
    projectResult(mode);
  }
}
window.addEventListener('ov:auth-required', authRequired);
window.addEventListener('storage', authStorageChanged);
const unsubscribeBackend = subscribeBackendStatus(synchronizeBackend);
synchronizeBackend();
export function clearLongformDraftForReset(): void {
  persistenceClosed = true;
  controller?.abort();
  for (const mode of ['stories', 'audiobook'] as const) {
    invalidateResults(mode);
    projectResult(mode);
  }
  ownerRecords.clear();
  currentOwner = null;
  storage.suspendJsonWrites((candidate) => candidate === key);
  localStorage.removeItem(key);
}
let controller: AbortController | null = null;
export function stopLongform() {
  if (controller) patch({ stopped: true });
  controller?.abort();
}
export function renderBody(mode: Mode, draft: Draft) {
  const names = parseCastNames(
    mode === 'audiobook' ? draft.script : draft.lines.map((line) => line.text).join('\n'),
  );
  const voice_map = Object.fromEntries(
    names
      .filter((name) => castVoice(draft.voiceCast, name))
      .map((name) => [name, castVoice(draft.voiceCast, name)]),
  );
  const common = {
    ...overridesToRequest(draft.overrides, draft.language),
    default_voice: draft.voice,
    voice_map,
    format: draft.format,
    language: draft.language,
    metadata: { ...draft.metadata, title: draft.title },
    loudness: draft.loudness,
    cover_path: draft.cover?.path ?? null,
  };
  return mode === 'audiobook'
    ? { ...common, text: draft.script, lexicon: lexiconMap(draft.lexicon) }
    : {
        ...common,
        chapters: storyToSpans(draft.lines, draft.cast, draft.globalSpeed),
      };
}
export async function renderLongform(mode: Mode, resumeId?: string) {
  if (controller || persistenceClosed) return;
  const current = new AbortController();
  controller = current;
  const owner = currentOwner;
  const generation = draftGeneration[mode];
  const connection = connectionRevision;
  const operation = ++renderSequence;
  latestRender[mode].set(owner, operation);
  const mayPresent = () =>
    !persistenceClosed &&
    controller === current &&
    draftGeneration[mode] === generation &&
    connectionRevision === connection &&
    !current.signal.aborted;
  const finishActivity = beginAppActivity('longform');
  patch({
    active: mode,
    stage: 'starting',
    completed: 0,
    total: 0,
    failed: 0,
    error: null,
    failure: null,
    chapters: [],
    stopped: false,
    progress: null,
    startedAt: Date.now(),
    lastActivityAt: Date.now(),
  });
  let done = false;
  let outputChapters: AudiobookRenderChapter[] = [];
  try {
    const draft = longformSession.state.drafts[mode];
    if (
      !resumeId &&
      !cachedTtsLanguagesSupported(queryClient, mode === 'stories' ? 'longform' : 'audiobook', [
        draft.language,
      ])
    ) {
      throw new Error(tr('languagePicker.chooseSupported'));
    }
    const response = await apiFetch(
      resumeId
        ? '/audiobook/resume/' + encodeURIComponent(resumeId)
        : mode === 'stories'
          ? '/longform/render'
          : '/audiobook',
      {
        method: 'POST',
        signal: current.signal,
        ...(resumeId
          ? {}
          : {
              body: JSON.stringify(renderBody(mode, draft)),
              headers: { 'Content-Type': 'application/json' },
            }),
      },
    );
    await consumeLongformStream(
      response,
      (event) => {
        if (current.signal.aborted || persistenceClosed || draftGeneration[mode] !== generation)
          return;
        if (event.type === 'error') {
          const failure = publicFailureFromEvent(event, 'Render failed');
          if (mayPresent()) patch({ failure });
          throw new Error(failure.reason);
        }
        if (event.type === 'started') {
          outputChapters = Array.from({ length: Number(event.chapters) || 0 }, () => ({
            title: '',
            status: 'pending',
          }));
          if (mayPresent())
            patch({
              stage: 'rendering',
              startedAt: Date.now(),
              lastActivityAt: Date.now(),
              total: Number(event.chapters) || 0,
              chapters: outputChapters.map((chapter, index) => ({
                ...chapter,
                status: index === 0 ? 'rendering' : 'pending',
              })),
            });
        }
        if (event.type === 'progress') {
          const progress = parseProgressEvent(event, Date.now());
          if (progress && mayPresent()) patch({ progress, lastActivityAt: progress.receivedAt });
        }
        if (event.type === 'chapter' || event.type === 'chapter_error') {
          const index = Number(event.index);
          if (Number.isInteger(index) && index >= 0 && index < outputChapters.length) {
            const exactMilliseconds = optionalFiniteNumber(event.duration_ms);
            const chapterUpdate: AudiobookRenderChapter = {
              title: typeof event.title === 'string' ? event.title : '',
              status: event.type === 'chapter_error' ? 'failed' : event.cached ? 'cached' : 'done',
              ...(Number.isFinite(Number(event.duration_s))
                ? { duration_s: Number(event.duration_s) }
                : {}),
              ...(event.type === 'chapter_error'
                ? {
                    error:
                      typeof event.reason === 'string'
                        ? event.reason
                        : typeof event.error === 'string'
                          ? event.error
                          : '',
                  }
                : {}),
            };
            if (exactMilliseconds !== undefined) chapterUpdate.duration_ms = exactMilliseconds;
            outputChapters[index] = chapterUpdate;
          }
          if (mayPresent())
            patch({
              lastActivityAt: Date.now(),
              completed: Number(event.index) + 1,
              chapters: outputChapters.map((chapter, chapterIndex) => ({
                ...chapter,
                status:
                  chapterIndex === index + 1 && chapter.status === 'pending'
                    ? 'rendering'
                    : chapter.status,
              })),
            });
        }
        if (event.type === 'assembling' && mayPresent()) patch({ stage: 'assembling' });
        if (event.type === 'stopped') {
          done = true;
          if (mayPresent()) patch({ stopped: true });
        }
        if (event.type === 'done' && typeof event.output === 'string' && event.output) {
          done = true;
          const failed = Array.isArray(event.failed_chapters)
            ? event.failed_chapters.length
            : Number(event.failed_chapters) || 0;
          const result: CompletedResult = {
            output: event.output,
            outputOwner: owner,
            outputScript: mode === 'audiobook' && !resumeId ? draft.script : '',
            outputChapters: outputChapters.map((chapter) => ({ ...chapter })),
            outputCachedChapters: Number(event.cached_chapters) || 0,
            outputFailedChapters: failed,
          };
          if (
            !persistenceClosed &&
            draftGeneration[mode] === generation &&
            latestRender[mode].get(owner) === operation
          ) {
            if (owner) resultSlots[mode].set(owner, result);
            else legacyResults[mode] = result;
            projectResult(mode);
            queuePersistence();
          }
          if (mayPresent()) patch({ failed });
        }
      },
      { signal: current.signal },
    );
    if (!done && !current.signal.aborted) throw new Error('Render stream ended before completion');
  } catch (error) {
    if (mayPresent()) patch({ error: error instanceof Error ? error.message : String(error) });
  } finally {
    finishActivity();
    if (controller === current) {
      controller = null;
      patch({ active: null, stage: '' });
    }
  }
}

export function dismissLongformError(): void {
  patch({ error: null, failure: null });
}

export function chapterPreviewBody(draft: Draft, chapter_index: number) {
  const body = renderBody('audiobook', draft);
  return {
    ...overridesToRequest(draft.overrides, draft.language),
    text: draft.script,
    chapter_index,
    default_voice: body.default_voice,
    voice_map: body.voice_map,
    language: body.language,
    lexicon: lexiconMap(draft.lexicon),
  };
}
