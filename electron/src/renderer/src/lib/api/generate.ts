import { generationFailureMessage } from '../../../../../../frontend/src/utils/generationFailureMessage.ts';
import i18next from 'i18next';
import { languageRejectionMessage } from '../../../../../../frontend/src/utils/languageRejection.ts';
import { ApiError, apiFetch, isAbortError } from './client';
import type { CloneGenerateInput, GenerateResult } from './types';
import { beginAppActivity } from '@/lib/app-activity';
import { createStreamingPreview } from '@/lib/audio/streaming-preview';

/** Upper end of the recommended 5–15 s reference; longer clips get an engine-aware note. */
export const CLONE_MAX_SECONDS = 15;
/**
 * Hard cap. OmniVoice's automatic passage selection examines at most five 15 s
 * windows (omnivoice/utils/audio.py CLONE_REF_MAX_WINDOWS) — rejected outright.
 */
export const REF_HARD_MAX_SECONDS = 75;
/**
 * Client-side abort backstop. The first /generate may cold-load the model;
 * the backend bounds that itself and returns a descriptive error, so this sits
 * just above its load timeout to make sure the UI never spins forever if the
 * backend goes silent.
 */
export const GENERATE_ABORT_MS = 21 * 60 * 1000;

// ── Instruct whitelist ─────────────────────────────────────────────────────
// The engine validator (omnivoice/models/omnivoice.py::_resolve_instruct)
// accepts ONLY these tags, one per category; anything else 400s with
// "Unsupported instruct items". Dialect names are engine vocabulary and are
// written as escapes so the source stays ASCII (tests/test_no_hardcoded_cjk.py).
const CATEGORIES: Readonly<Record<string, readonly string[]>> = {
  Gender: ['male', 'female'],
  Age: ['child', 'teenager', 'young adult', 'middle-aged', 'elderly'],
  Pitch: ['very low pitch', 'low pitch', 'moderate pitch', 'high pitch', 'very high pitch'],
  Style: ['whisper'],
  EnglishAccent: [
    'american accent',
    'british accent',
    'australian accent',
    'canadian accent',
    'indian accent',
    'chinese accent',
    'korean accent',
    'japanese accent',
    'portuguese accent',
    'russian accent',
  ],
  ChineseDialect: [
    '\u6CB3\u5357\u8BDD',
    '\u9655\u897F\u8BDD',
    '\u56DB\u5DDD\u8BDD',
    '\u8D35\u5DDE\u8BDD',
    '\u4E91\u5357\u8BDD',
    '\u6842\u6797\u8BDD',
    '\u6D4E\u5357\u8BDD',
    '\u77F3\u5BB6\u5E84\u8BDD',
    '\u7518\u8083\u8BDD',
    '\u5B81\u590F\u8BDD',
    '\u9752\u5C9B\u8BDD',
    '\u4E1C\u5317\u8BDD',
  ],
};

// EnglishAccent and ChineseDialect are separate categories but the engine
// rejects an instruct that sets both ("Cannot mix Chinese dialect and English
// accent"). Grouping them here enforces that exclusivity client-side; a future
// exclusive pair joins by adding an entry.
const EXCLUSIVE_GROUPS: Readonly<Record<string, string>> = {
  EnglishAccent: 'accent_dialect',
  ChineseDialect: 'accent_dialect',
};

const TAG_TO_CATEGORY: ReadonlyMap<string, string> = new Map(
  Object.entries(CATEGORIES).flatMap(([category, tags]) =>
    tags.map((tag) => [tag.toLowerCase(), category] as const),
  ),
);

function groupOf(category: string): string {
  return EXCLUSIVE_GROUPS[category] ?? category;
}

// Users type the fullwidth comma (U+FF0C) on CJK keyboards.
const ITEM_SEPARATOR = /[,\uFF0C]/;

export interface SanitizedInstruct {
  /** Comma-joined valid tags, one per category, in the order they were accepted. */
  instruct: string;
  /** Free-text items that are not whitelist tags (prose descriptions). */
  unsupported: string[];
  /** Valid tags whose category was already claimed by an earlier item. */
  duplicates: string[];
  /** Valid tags whose exclusive group (accent vs dialect) was already claimed. */
  conflicts: string[];
}

/**
 * Reduce a free-text style field to a validator-safe instruct. Port of the
 * main app's `buildDesignInstruct` with no picker state (clone has no design
 * sliders): first valid tag per category wins, the rest are bucketed so the
 * caller can tell the user exactly what was dropped instead of round-tripping
 * a 400 from the engine.
 */
export function sanitizeInstruct(free: string): SanitizedInstruct {
  const byCategory = new Map<string, string>();
  const claimedGroups = new Set<string>();
  const unsupported: string[] = [];
  const duplicates: string[] = [];
  const conflicts: string[] = [];

  for (const raw of String(free ?? '').split(ITEM_SEPARATOR)) {
    const item = raw.trim();
    if (!item) continue;
    const normalized = item.toLowerCase();
    const category = TAG_TO_CATEGORY.get(normalized);
    if (!category) {
      unsupported.push(item);
    } else if (byCategory.has(category)) {
      duplicates.push(item);
    } else if (claimedGroups.has(groupOf(category))) {
      conflicts.push(item);
    } else {
      byCategory.set(category, normalized);
      claimedGroups.add(groupOf(category));
    }
  }

  return { instruct: [...byCategory.values()].join(', '), unsupported, duplicates, conflicts };
}

// ── Request ────────────────────────────────────────────────────────────────

/** Multipart body for `POST /generate` (classic whole-file path). */
export function toGenerateForm(input: CloneGenerateInput): FormData {
  const form = new FormData();
  form.append('text', input.text);
  if (input.seed !== undefined && Number.isInteger(input.seed))
    form.append('seed', String(input.seed));
  // Auto must be explicit: omission inherits the saved profile's default.
  if (input.language) form.append('language', input.language);
  form.append('num_step', String(input.steps));
  form.append('guidance_scale', String(input.cfg));
  form.append('speed', String(input.speed));
  form.append('denoise', String(input.denoise));
  form.append('t_shift', String(input.tShift));
  form.append('position_temperature', String(input.posTemp));
  form.append('class_temperature', String(input.classTemp));
  form.append('layer_penalty_factor', String(input.layerPenalty));
  form.append('postprocess_output', String(input.postprocess));
  const duration = Number.parseFloat(input.duration);
  if (input.duration && Number.isFinite(duration)) form.append('duration', String(duration));

  if (input.profileId) {
    form.append('profile_id', input.profileId);
  } else if (input.refAudio) {
    const name =
      input.refAudioName ||
      (input.refAudio instanceof File ? input.refAudio.name : '') ||
      'audio.wav';
    form.append('ref_audio', input.refAudio, name);
    form.append('ref_text', input.refText ?? '');
  }

  if (input.instruct) {
    const { instruct } = sanitizeInstruct(input.instruct);
    if (instruct) form.append('instruct', instruct);
  }
  return form;
}

// ── Response ───────────────────────────────────────────────────────────────

function headerFloat(headers: Headers, name: string): number | null {
  const value = Number.parseFloat(headers.get(name) ?? '');
  return Number.isFinite(value) ? value : null;
}

function headerInt(headers: Headers, name: string): number | null {
  const value = Number.parseInt(headers.get(name) ?? '', 10);
  return Number.isInteger(value) ? value : null;
}

/** Everything `/generate` reports about a take rides in response headers. */
export function parseGenerateHeaders(headers: Headers): Omit<GenerateResult, 'blob'> {
  const routingStatus = headers.get('X-OmniVoice-Routing');
  const droppedCount = headerInt(headers, 'X-OmniVoice-Dropped-Chunks');
  return {
    id: headers.get('X-Audio-Id'),
    audioPath: headers.get('X-Audio-Path'),
    durationSeconds: headerFloat(headers, 'X-Audio-Duration'),
    genTimeSeconds: headerFloat(headers, 'X-Gen-Time'),
    seed: headerInt(headers, 'X-Seed'),
    routing: routingStatus
      ? { status: routingStatus, reason: headers.get('X-OmniVoice-Routing-Reason') ?? '' }
      : null,
    dropped:
      droppedCount !== null && droppedCount > 0
        ? { count: droppedCount, text: headers.get('X-OmniVoice-Dropped-Text') ?? '' }
        : null,
  };
}

async function readAudioBody(
  res: Response,
  onProgress?: (pct: number | null) => void,
): Promise<Blob> {
  if (!res.body) return res.blob();
  const contentLength = Number.parseInt(res.headers.get('Content-Length') ?? '0', 10);
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    if (contentLength > 0) onProgress?.(Math.round((received / contentLength) * 100));
  }
  return new Blob(chunks as BlobPart[], { type: 'audio/wav' });
}

export interface GenerateOptions {
  signal?: AbortSignal;
  /** Download progress 0..100 once Content-Length is known; never called otherwise. */
  onProgress?: (pct: number | null) => void;
}

export class StreamingPreviewError extends Error {
  readonly retryable: boolean;
  readonly terminal: boolean;
  /**
   * The backend exception TYPE behind an otherwise generic failure. Every
   * unclassified engine failure renders one fixed floor message, so without
   * this an auto-filed report cannot be told apart from any other (#1800).
   * Never the exception message — only its class name.
   */
  readonly errorClass: string | null;
  constructor(
    message: string,
    options: { retryable?: boolean; terminal?: boolean; errorClass?: unknown } = {},
  ) {
    super(message);
    this.name = 'StreamingPreviewError';
    this.retryable = options.retryable === true;
    this.terminal = options.terminal === true;
    this.errorClass = typeof options.errorClass === 'string' ? options.errorClass : null;
  }
}

export function shouldFallbackToClassic(error: unknown): boolean {
  return error instanceof StreamingPreviewError && !error.retryable && !error.terminal;
}

export async function resolveRemoteTtsTarget(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await apiFetch('/workers/target?op=tts', { signal });
    const payload = (await response.json()) as { active?: { remote?: boolean } };
    return payload.active?.remote === true;
  } catch {
    return false;
  }
}

interface StreamEvent {
  type: 'start' | 'chunk' | 'done' | 'warning' | 'progress' | 'error';
  sample_rate?: number;
  crossfade_ms?: number;
  total_chunks?: number;
  pcm?: string;
  id?: string;
  audio_path?: string;
  duration?: number;
  gen_time?: number;
  seed?: number;
  count?: number;
  text?: string[];
  detail?: string;
  code?: string;
  language?: string;
  docs_topic?: string;
  error_class?: unknown;
  terminal?: boolean;
  retryable?: boolean;
  percent?: number;
}

/** Stream PCM for immediate preview, then return the canonical saved WAV. */
export async function generateCloneStreaming(
  input: CloneGenerateInput,
  opts: GenerateOptions = {},
): Promise<GenerateResult> {
  const form = toGenerateForm(input);
  form.append('stream', 'true');
  const finishActivity = beginAppActivity('synthesis');
  let player: ReturnType<typeof createStreamingPreview> | null = null;
  let meta: StreamEvent | null = null;
  let dropped: GenerateResult['dropped'] = null;
  try {
    const response = await apiFetch('/generate', {
      method: 'POST',
      body: form,
      signal: opts.signal,
    });
    const headers = parseGenerateHeaders(response.headers);
    if (!response.body) throw new StreamingPreviewError('TTS stream has no response body');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffered = '';
    let received = 0;
    let total = 0;
    const handle = (event: StreamEvent) => {
      if (event.type === 'start') {
        if (!event.sample_rate) throw new StreamingPreviewError('TTS stream has no sample rate');
        total = event.total_chunks ?? 0;
        player = createStreamingPreview(event.sample_rate, event.crossfade_ms);
      } else if (event.type === 'chunk') {
        if (!event.pcm || !player) throw new StreamingPreviewError('Invalid TTS audio chunk');
        player.appendPcm16Base64(event.pcm);
        received += 1;
        if (total > 0) opts.onProgress?.(Math.min(100, Math.round((received / total) * 100)));
      } else if (event.type === 'progress' && typeof event.percent === 'number') {
        opts.onProgress?.(Math.max(0, Math.min(100, event.percent)));
      } else if (event.type === 'warning' && event.count) {
        dropped = { count: event.count, text: (event.text ?? []).join(' | ') };
      } else if (event.type === 'done') {
        meta = event;
      } else if (event.type === 'error') {
        const message =
          languageRejectionMessage(event, i18next.t) ||
          generationFailureMessage(event, i18next.t) ||
          event.detail ||
          'TTS stream reported an error';
        const terminal =
          event.terminal === true ||
          ['[clone_ref_unusable]', '[clone_ref_too_long]', '[clone_ref_no_speech]'].some((marker) =>
            message.includes(marker),
          );
        throw new StreamingPreviewError(message, {
          retryable: event.retryable,
          terminal,
          errorClass: event.error_class,
        });
      }
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true });
      let newline = buffered.indexOf('\n');
      while (newline >= 0) {
        const line = buffered.slice(0, newline).trim();
        buffered = buffered.slice(newline + 1);
        if (line) handle(JSON.parse(line) as StreamEvent);
        newline = buffered.indexOf('\n');
      }
    }
    buffered += decoder.decode();
    if (buffered.trim()) handle(JSON.parse(buffered) as StreamEvent);
    const completed = meta as StreamEvent | null;
    const activePlayer = player as ReturnType<typeof createStreamingPreview> | null;
    if (!completed?.audio_path) {
      throw new StreamingPreviewError('TTS stream ended without a completion event');
    }
    activePlayer?.finalize();
    let blob: Blob;
    try {
      const audio = await apiFetch('/audio/' + encodeURIComponent(completed.audio_path), {
        signal: opts.signal,
      });
      blob = await audio.blob();
    } catch (error) {
      throw new StreamingPreviewError(error instanceof Error ? error.message : String(error), {
        terminal: true,
      });
    }
    return {
      blob,
      id: completed.id ?? headers.id,
      audioPath: completed.audio_path,
      durationSeconds: completed.duration ?? null,
      genTimeSeconds: completed.gen_time ?? null,
      seed: completed.seed ?? headers.seed,
      routing: headers.routing,
      dropped,
    };
  } catch (error) {
    (player as ReturnType<typeof createStreamingPreview> | null)?.fail();
    if (error instanceof StreamingPreviewError || error instanceof ApiError || isAbortError(error))
      throw error;
    throw new StreamingPreviewError(error instanceof Error ? error.message : String(error));
  } finally {
    finishActivity();
  }
}

/** Synthesize one take through the classic whole-file path. */
export async function generateClone(
  input: CloneGenerateInput,
  opts: GenerateOptions = {},
): Promise<GenerateResult> {
  const form = toGenerateForm(input);
  const controller = new AbortController();
  const forwardAbort = (): void => controller.abort();
  if (opts.signal?.aborted) controller.abort();
  else opts.signal?.addEventListener('abort', forwardAbort, { once: true });
  const backstop = setTimeout(forwardAbort, GENERATE_ABORT_MS);
  const finishActivity = beginAppActivity('synthesis');
  try {
    const res = await apiFetch('/generate', {
      method: 'POST',
      body: form,
      signal: controller.signal,
    });
    const blob = await readAudioBody(res, opts.onProgress);
    return { blob, ...parseGenerateHeaders(res.headers) };
  } finally {
    finishActivity();
    clearTimeout(backstop);
    opts.signal?.removeEventListener('abort', forwardAbort);
  }
}
