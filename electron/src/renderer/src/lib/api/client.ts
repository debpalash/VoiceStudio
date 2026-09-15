/**
 * Same-origin API client. The renderer never talks to 127.0.0.1:<port>
 * directly (CORS); `/api/*` is proxied by the dev server / the app:// protocol
 * handler in main (see CONTRACT.md).
 */
import type { ApiErrorPayload } from './types';
import { tr } from '@/lib/i18n-text';
import { getBackendStatusSnapshot } from '@/hooks/use-backend-status';
import { recordBackendContact } from '../../../../../../frontend/src/utils/backendContact';

export const API_BASE = '/api';

export class ApiError extends Error {
  readonly status: number;
  /** Human-readable detail (the backend's `detail`, JSON-stringified when structured). */
  readonly detail: string;
  /** The parsed JSON error body when there was one. */
  readonly payload: ApiErrorPayload | null;

  constructor(status: number, detail: string, payload: ApiErrorPayload | null = null) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.payload = payload;
  }
}

export function apiPath(path: string): string {
  return `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`;
}

export function isAbortError(err: unknown): boolean {
  return (
    typeof err === 'object' && err !== null && (err as { name?: unknown }).name === 'AbortError'
  );
}

/** Message for a toast: the ApiError detail, an Error's message, or the stringified value. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return String(err);
}

function detailToString(detail: unknown): string {
  if (detail && typeof detail === 'object' && 'code' in detail && detail.code === 'dub_background_unavailable')
    return tr('dubIntegrity.backgroundUnavailable');
  if (typeof detail === 'string') return detail;
  if (detail == null) return '';
  if (
    typeof detail === 'object' &&
    'message' in detail &&
    typeof detail.message === 'string' &&
    detail.message.trim()
  )
    return detail.message;
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

/** Build the ApiError for a non-2xx response: JSON `detail` when present, else the body text. */
export async function errorFromResponse(res: Response): Promise<ApiError> {
  let text = '';
  try {
    text = await res.text();
  } catch {
    // Body already consumed or unreadable — fall through to the status line.
  }
  let payload: ApiErrorPayload | null = null;
  if (text) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        payload = parsed as ApiErrorPayload;
      }
    } catch {
      // Plain-text body.
    }
  }
  const detail = payload && 'detail' in payload ? detailToString(payload.detail) : text.trim();
  const statusLine = `HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ''}`;
  return new ApiError(res.status, detail || statusLine, payload);
}

/**
 * fetch() against the API. Throws ApiError on any non-2xx response and an
 * ApiError with status 0 when the backend is unreachable. Deliberate aborts
 * are re-thrown untouched so callers can tell them apart.
 */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  if (
    typeof window !== 'undefined' &&
    window.voicestudio?.backend &&
    getBackendStatusSnapshot().stage !== 'ready'
  )
    throw new ApiError(0, tr('tts_errors.backend_unreachable'));
  let res: Response;
  try {
    res = await fetch(apiPath(path), init);
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw new ApiError(0, tr('tts_errors.backend_unreachable'));
  }
  // Any HTTP response, including an error status, proves the backend answered.
  recordBackendContact();
  if (!res.ok) throw await errorFromResponse(res);
  return res;
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  // String bodies on this helper are serialized JSON; never label FormData,
  // whose multipart boundary must be supplied by the browser.
  if (typeof init?.body === 'string') {
    const headers = new Headers(init.headers);
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    init = { ...init, headers };
  }
  const res = await apiFetch(path, init);
  return (await res.json()) as T;
}

/** Playable URL for a generated take (`audio_path` from history / X-Audio-Path). */
export function audioUrl(filename: string): string {
  return `${API_BASE}/audio/${encodeURIComponent(filename)}`;
}

/** Playable URL for a saved voice's reference clip. */
export function profileAudioUrl(id: string): string {
  return `${API_BASE}/profiles/${encodeURIComponent(id)}/audio`;
}
