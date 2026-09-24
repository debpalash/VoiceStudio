import {
  DEFAULT_OVERRIDES,
  overridesToRequest,
  type Overrides,
} from '../../../../../../frontend/src/utils/longformOverrides';
import { castVoice } from './cast-map';
import { parseCastNames } from '../../../../../../frontend/src/utils/audiobookScript';
import { restoreBookOptions, lexiconMap, type BookOptions } from './book-options';
import { createCoalescedJsonStorage } from '../../../../../../frontend/src/utils/coalescedJsonStorage';
import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import { apiFetch } from '@/lib/api/client';
import { consumeLongformStream } from '../../../../../../frontend/src/utils/longformStream';
import { storyToSpans } from '../../../../../../frontend/src/utils/storyToSpans';
import { beginAppActivity } from '@/lib/app-activity';
import { publicFailureFromEvent, type PublicFailure } from '@/lib/api/failure';
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
  /** Exact length in the embedded m4b chapters; sent by newer backends only. */
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
  outputScript: '',
  outputChapters: [],
  outputCachedChapters: 0,
  outputFailedChapters: 0,
});
const key = 'voicestudio.longform.v1';
const drafts = {
  stories: blankLongformDraft(),
  audiobook: blankLongformDraft(),
};
try {
  const saved = JSON.parse(localStorage.getItem(key) || 'null');
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
        output: typeof s.output === 'string' ? s.output : '',
        outputScript: typeof s.outputScript === 'string' ? s.outputScript : '',
        outputChapters: Array.isArray(s.outputChapters)
          ? s.outputChapters.filter(
              (chapter: AudiobookRenderChapter) =>
                chapter && typeof chapter.title === 'string' && typeof chapter.status === 'string',
            )
          : [],
        outputCachedChapters:
          typeof s.outputCachedChapters === 'number' ? s.outputCachedChapters : 0,
        outputFailedChapters:
          typeof s.outputFailedChapters === 'number' ? s.outputFailedChapters : 0,
      };
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
});
export const useLongformSession = () => useStore(longformSession);
/** Fences document imports that finish after a new dub replaces the Stories draft. */
export const storiesImportEpoch = { current: 0 };
const patch = (value: Partial<Session>) => longformSession.setState((s) => ({ ...s, ...value }));
export function editLongform(mode: Mode, value: Partial<Draft>) {
  if (longformSession.state.active) return;
  updateDraft(mode, value);
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
    controller?.abort();
  });
function updateDraft(mode: Mode, value: Partial<Draft>) {
  const next = {
    ...longformSession.state.drafts,
    [mode]: { ...longformSession.state.drafts[mode], ...value },
  };
  patch({ drafts: next });
  storage.queueJsonWrite(key, () => longformSession.state.drafts);
}

/** Prevent queued and pagehide writes from recreating a draft during a confirmed data reset. */
export function clearLongformDraftForReset(): void {
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
  if (controller) return;
  const current = new AbortController();
  controller = current;
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
  });
  let done = false;
  let outputChapters: AudiobookRenderChapter[] = [];
  try {
    const draft = longformSession.state.drafts[mode];
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
        if (event.type === 'error') {
          const failure = publicFailureFromEvent(event, 'Render failed');
          patch({ failure });
          throw new Error(failure.reason);
        }
        if (event.type === 'started') {
          outputChapters = Array.from({ length: Number(event.chapters) || 0 }, () => ({
            title: '',
            status: 'pending',
          }));
          patch({
            stage: 'rendering',
            total: Number(event.chapters) || 0,
            chapters: outputChapters.map((chapter, index) => ({
              ...chapter,
              status: index === 0 ? 'rendering' : 'pending',
            })),
          });
        }
        if (event.type === 'chapter' || event.type === 'chapter_error') {
          const index = Number(event.index);
          if (Number.isInteger(index) && index >= 0 && index < outputChapters.length)
            outputChapters[index] = {
              title: typeof event.title === 'string' ? event.title : '',
              status: event.type === 'chapter_error' ? 'failed' : event.cached ? 'cached' : 'done',
              ...(Number.isFinite(Number(event.duration_s))
                ? { duration_s: Number(event.duration_s) }
                : {}),
              ...(event.duration_ms != null && Number.isFinite(Number(event.duration_ms))
                ? { duration_ms: Number(event.duration_ms) }
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
          patch({
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
        if (event.type === 'assembling') patch({ stage: 'assembling' });
        if (event.type === 'stopped') {
          done = true;
          patch({ stopped: true });
        }
        if (event.type === 'done' && typeof event.output === 'string' && event.output) {
          done = true;
          const failed = Array.isArray(event.failed_chapters)
            ? event.failed_chapters.length
            : Number(event.failed_chapters) || 0;
          updateDraft(mode, {
            output: event.output,
            outputScript: mode === 'audiobook' && !resumeId ? draft.script : '',
            outputChapters: outputChapters.map((chapter) => ({ ...chapter })),
            outputCachedChapters: Number(event.cached_chapters) || 0,
            outputFailedChapters: failed,
          });
          patch({
            failed,
          });
        }
      },
      { signal: current.signal },
    );
    if (!done && !current.signal.aborted) throw new Error('Render stream ended before completion');
  } catch (error) {
    if (!current.signal.aborted)
      patch({ error: error instanceof Error ? error.message : String(error) });
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
