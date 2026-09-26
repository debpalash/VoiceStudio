import { API, apiUrl, apiJson, apiPost, apiFetch } from './client';
import type { DubHistoryResponse, DubTranslateResponse } from './types';

export async function dubUpload(
  file: File | Blob,
  jobId: string,
  {
    signal,
    inputType = 'video',
    sourceLang,
  }: { signal?: AbortSignal; inputType?: 'video' | 'audio'; sourceLang?: string } = {},
): Promise<unknown> {
  const fd = new FormData();
  fd.append('video', file);
  fd.append('job_id', jobId);
  fd.append('input_type', inputType); // #119: audio-only dubbing
  if (sourceLang && sourceLang !== 'auto') fd.append('source_lang', sourceLang);
  return apiPost('/dub/upload', fd, { signal });
}

export interface IngestUrlOptions {
  signal?: AbortSignal;
  /** Ask yt-dlp to also pull caption tracks (incl. YouTube auto-translations). */
  fetchSubs?: boolean;
  /** Limit caption fetch to specific lang codes; defaults to all available. */
  subLangs?: string[];
  /** Explicit cookies.txt export used only for this import. */
  cookieFile?: File;
  /** Explicit spoken language, or auto/undefined to use ASR detection. */
  sourceLang?: string;
}

export const DUB_COOKIE_TRANSPORT_ERROR = 'DUB_COOKIE_TRANSPORT';
export const DUB_COOKIE_SIZE_ERROR = 'DUB_COOKIE_TOO_LARGE';
export const MAX_COOKIE_EXPORT_BYTES = 1024 * 1024;

function cookieSelectionError(code: string): Error & { code: string } {
  return Object.assign(new Error(code), { code });
}

export function _cookieTransportAllowed(apiBase: string): boolean {
  const endpoint = new URL(apiBase, window.location.href);
  return (
    endpoint.protocol === 'https:' ||
    endpoint.hostname === 'localhost' ||
    endpoint.hostname === '127.0.0.1' ||
    endpoint.hostname === '[::1]'
  );
}

export async function dubIngestUrl(
  url: string,
  jobId: string,
  opts: IngestUrlOptions = {},
): Promise<unknown> {
  const { signal, fetchSubs, subLangs, cookieFile, sourceLang } = opts;
  if (cookieFile && !_cookieTransportAllowed(API)) {
    throw cookieSelectionError(DUB_COOKIE_TRANSPORT_ERROR);
  }
  if (cookieFile && cookieFile.size > MAX_COOKIE_EXPORT_BYTES) {
    throw cookieSelectionError(DUB_COOKIE_SIZE_ERROR);
  }
  const cookieText = cookieFile ? await cookieFile.text() : undefined;
  return apiPost(
    '/dub/ingest-url',
    {
      url,
      job_id: jobId,
      fetch_subs: fetchSubs || undefined,
      sub_langs: subLangs && subLangs.length ? subLangs : undefined,
      cookie_file: cookieText,
      source_lang: sourceLang && sourceLang !== 'auto' ? sourceLang : undefined,
    },
    { signal },
  );
}

export function transcribeStreamUrl(jobId: string, numSpeakers?: number | null): string {
  const base = `${API}/dub/transcribe-stream/${jobId}`;
  // Optional pyannote speaker-count hint (#274). Only appended when a positive
  // integer; otherwise the backend auto-detects.
  if (numSpeakers && Number.isFinite(numSpeakers) && numSpeakers > 0) {
    return `${base}?num_speakers=${Math.floor(numSpeakers)}`;
  }
  return base;
}

export async function dubAbort(jobId: string): Promise<void> {
  try {
    await apiFetch(`/dub/abort/${jobId}`, { method: 'POST' });
  } catch {
    /* best-effort */
  }
}

export async function dubCleanupSegments(jobId: string): Promise<unknown> {
  return apiPost(`/dub/cleanup-segments/${jobId}`);
}

export interface DubImportSrtResponse {
  segments: Array<{
    id: number;
    start: number;
    end: number;
    text: string;
    text_original: string;
    speaker_id: string;
  }>;
  stats: {
    imported: number;
    skipped_malformed: number;
    dropped_overlap: number;
    clamped_to_duration: number;
  };
}

export async function dubImportSrt(
  jobId: string,
  file: File | Blob,
  { signal }: { signal?: AbortSignal } = {},
): Promise<DubImportSrtResponse> {
  const fd = new FormData();
  fd.append('file', file);
  return apiPost<DubImportSrtResponse>(`/dub/import-srt/${jobId}`, fd, { signal });
}

export interface ParsedSubtitleCue {
  start: number;
  end: number;
  text: string;
}

export interface ParseSubtitleTextResponse {
  segments: ParsedSubtitleCue[];
  skipped_cues: number;
  dropped_overlaps: number;
}

/**
 * Parse pasted subtitle text (SRT/VTT-ish) into timed cues.
 *
 * Stateless: unlike `dubImportSrt` this touches no job and replaces no
 * segments — it exists so the "paste a translation" flow reuses the
 * backend's lenient cue parser instead of reimplementing it in JS.
 */
export async function dubParseSubtitleText(text: string): Promise<ParseSubtitleTextResponse> {
  return apiPost<ParseSubtitleTextResponse>('/dub/parse-subtitle-text', { text });
}

export async function dubTranslate(body: Record<string, unknown>): Promise<DubTranslateResponse> {
  return apiPost<DubTranslateResponse>('/dub/translate', body);
}

export async function dubGenerate(jobId: string, body: Record<string, unknown>): Promise<unknown> {
  return apiPost(`/dub/generate/${jobId}`, body);
}

/**
 * Query fragment for the hardsub options on /dub/download.
 *
 * Karaoke (word-highlight ASS burn) only applies to the single-line layout —
 * dual-layout karaoke is unsupported, so the flag is dropped when dual is on
 * (mirrors the backend guard and the disabled Export drawer control).
 */
export function dubBurnQuery(burnSubs: boolean, dualSubs: boolean, karaokeSubs: boolean): string {
  if (!burnSubs) return '';
  const karaoke = karaokeSubs && !dualSubs ? 1 : 0;
  return `&burn_subs=1&dual=${dualSubs ? 1 : 0}&karaoke=${karaoke}`;
}

export function tasksStreamUrl(taskId: string): string {
  return apiUrl(`/tasks/stream/${taskId}`);
}

export async function tasksCancel(taskId: string): Promise<Response> {
  return apiFetch(`/tasks/cancel/${taskId}`, { method: 'POST' });
}

export async function listDubHistory(): Promise<DubHistoryResponse> {
  return apiJson<DubHistoryResponse>('/dub/history');
}

export async function clearDubHistory(): Promise<Response> {
  return apiFetch('/dub/history', { method: 'DELETE' });
}

export interface DubTrackInfo {
  path?: string;
  language?: string;
  language_code?: string;
  duration?: number;
  timing_strategy?: string;
}

/** Per-track metadata (duration, timing strategy, …) keyed by language code.
 *  Backs the track-pill tooltips; the store only carries the track codes. */
/** Per-segment texts for one generated track ({segKey: text}, may be empty). */
export async function dubSegmentsText(
  jobId: string,
  lang: string,
): Promise<Record<string, string>> {
  const res = await apiJson<{ texts?: Record<string, string> }>(
    `/dub/segments-text/${encodeURIComponent(jobId)}?lang=${encodeURIComponent(lang)}`,
  );
  return res?.texts || {};
}

export async function dubListTracks(jobId: string): Promise<Record<string, DubTrackInfo>> {
  const res = await apiJson<{ tracks?: Record<string, DubTrackInfo> }>(
    `/dub/tracks/${encodeURIComponent(jobId)}`,
  );
  return res?.tracks || {};
}

export interface DubQCResponse {
  engine: string;
  total: number;
  flagged_count: number;
  drift_threshold: number;
  segments: {
    seg_id: string;
    drift: number;
    flagged: boolean;
    recognized_text: string;
    measured_start: number | null;
    measured_end: number | null;
  }[];
}

/** Wave 3.3: second-pass ASR QC — re-recognize the dubbed audio and flag
 *  lines whose recognized text drifts from the target. Non-destructive. */
export async function dubQc(
  jobId: string,
  lang?: string,
  driftThreshold?: number,
): Promise<DubQCResponse> {
  const qs = new URLSearchParams();
  if (lang) qs.set('lang', lang);
  if (driftThreshold != null) qs.set('drift_threshold', String(driftThreshold));
  const suffix = qs.toString() ? `?${qs}` : '';
  return apiPost<DubQCResponse>(`/dub/qc/${jobId}${suffix}`);
}
