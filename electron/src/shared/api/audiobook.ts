import { apiFetch } from './client';

interface AudiobookSpan {
  voice_id: string | null;
  text: string;
  pause_ms_after: number;
}
interface AudiobookChapter {
  title: string;
  char_count: number;
  spans: AudiobookSpan[];
}
export interface AudiobookPlan {
  chapters: AudiobookChapter[];
  chapter_count: number;
  char_count: number;
}

/** Parse a script into a chapter/span plan (pure preview, no synthesis). */
export async function audiobookPlan(body: {
  text: string;
  default_voice?: string | null;
}): Promise<AudiobookPlan> {
  const res = await apiFetch('/audiobook/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

export interface AudiobookPreview {
  output: string; // path under OUTPUTS_DIR, served via /audio
  duration_s: number;
  cached: boolean;
  title: string;
}

/**
 * Expressive/quality knobs shared by the audiobook synth + per-chapter preview
 * (#1208). All optional — an omitted field reproduces today's exact render on
 * the backend. Preview MUST carry the same fields as the full render so a
 * previewed chapter warms exactly the cache slot the render reuses.
 */
export interface ExpressiveRequestFields {
  num_step?: number | null;
  guidance_scale?: number | null;
  position_temperature?: number | null;
  class_temperature?: number | null;
  postprocess_output?: boolean | null;
  seed?: number | null;
  emo_vector?: number[] | null;
  emo_text?: string | null;
  emo_alpha?: number | null;
  vary_repeats?: boolean;
}

/** Render a single chapter to audition it (also warms the resume cache). */
export async function audiobookPreviewChapter(
  body: {
    text: string;
    chapter_index: number;
    default_voice?: string | null;
    language?: string | null;
    lexicon?: Record<string, string> | null;
    // Cast map {[voice:NAME] → profile id} — must match the render's (#1217).
    voice_map?: Record<string, string> | null;
  } & ExpressiveRequestFields,
): Promise<AudiobookPreview> {
  const res = await apiFetch('/audiobook/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

/** Global tags embedded in the output file (player-visible). */
interface AudiobookMetadata {
  title?: string;
  author?: string;
  narrator?: string;
  year?: string;
  genre?: string;
  description?: string;
}

export interface AudiobookGenerateBody extends ExpressiveRequestFields {
  text: string;
  default_voice?: string | null;
  // #1208 / #505: the backend AudiobookRequest has always accepted `language`,
  // but this body omitted it, so an audiobook language pick could never reach
  // the backend. Now threaded through ('Auto' → the profile's language).
  language?: string | null;
  bitrate?: string;
  format?: 'm4b' | 'mp3';
  loudness?: 'off' | 'acx' | 'podcast' | null;
  cover_path?: string | null;
  metadata?: AudiobookMetadata | null;
  lexicon?: Record<string, string> | null;
  // Multi-voice cast map {[voice:NAME] → profile id} (#1217). Absent/empty
  // reproduces today's single-voice render + cache keys.
  voice_map?: Record<string, string> | null;
}

/**
 * Start the synth job. Returns the raw streaming Response; the caller reads
 * `response.body` with a reader + the sseParse helpers. (apiFetch throws on a
 * non-2xx status, so a returned Response is always a live stream.)
 *
 * `opts.signal` wires an AbortController into the fetch so a Stop cancels the
 * request end-to-end (#1216): aborting closes the connection, the backend's
 * `is_disconnected()` poll trips, and it stops scheduling further chapters
 * instead of rendering the whole book into a stream nobody is reading.
 */
export async function audiobookGenerate(
  body: AudiobookGenerateBody,
  opts: { signal?: AbortSignal } = {},
): Promise<Response> {
  return apiFetch('/audiobook', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: opts.signal,
  });
}

export interface ResumableAudiobookJob {
  job_id: string;
  type: 'audiobook' | 'longform';
  status: string;
  title: string;
  total_chapters: number;
  chapters_done: number;
  created_at: number | null;
}

/** List server-owned longform manifests left by interrupted renders. */
export async function audiobookListJobs(): Promise<{ jobs: ResumableAudiobookJob[] }> {
  const res = await apiFetch('/audiobook/jobs');
  return res.json();
}

/** Resume one trusted server-side manifest and stream the normal audiobook events. */
export async function audiobookResume(
  jobId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<Response> {
  return apiFetch(`/audiobook/resume/${encodeURIComponent(jobId)}`, {
    method: 'POST',
    signal: opts.signal,
  });
}

/** Upload a cover image; returns the server-side path to pass as `cover_path`. */
export async function audiobookUploadCover(file: File): Promise<{ path: string }> {
  const form = new FormData();
  form.append('cover', file);
  const res = await apiFetch('/audiobook/cover', { method: 'POST', body: form });
  return res.json();
}

/** Import a .txt/.md/.epub/.pdf into a chapter-delimited script. */
export async function audiobookImport(file: File): Promise<{ text: string; chapters: number }> {
  const form = new FormData();
  form.append('file', file);
  const res = await apiFetch('/audiobook/import', { method: 'POST', body: form });
  return res.json();
}

export interface LongformRenderBody extends ExpressiveRequestFields {
  chapters: Array<{
    title?: string;
    spans: Array<{
      voice_id: string | null;
      text: string;
      pause_ms_after: number;
      speed?: number | null;
    }>;
  }>;
  default_voice?: string | null;
  language?: string | null;
  bitrate?: string;
  format?: 'm4b' | 'mp3';
  loudness?: 'off' | 'acx' | 'podcast' | null;
  cover_path?: string | null;
  metadata?: AudiobookMetadata | null;
  voice_map?: Record<string, string> | null;
}

/**
 * Render a pre-built chapter/span plan through the shared chapterized renderer
 * (the convergence endpoint Stories posts to). Returns the raw SSE stream
 * Response — read `response.body` with the sseParse helpers, same as
 * `audiobookGenerate`.
 */
export async function longformRender(body: LongformRenderBody): Promise<Response> {
  return apiFetch('/longform/render', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
