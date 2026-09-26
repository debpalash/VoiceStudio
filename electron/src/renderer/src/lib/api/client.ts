import { generationFailureMessage } from '@shared/utils/generationFailureMessage.ts';
import { languageRejectionMessage } from '@shared/utils/languageRejection.ts';
/** Native builds use Electron's `/api` protocol proxy. The production web
 * bundle is served by FastAPI itself, whose routes live at the origin root. */
import type { ApiErrorPayload } from './types';
import { tr } from '@/lib/i18n-text';
import { getBackendStatusSnapshot } from '@/hooks/use-backend-status';
import { recordBackendContact } from '@shared/utils/backendContact';
import {
  clearAdminSession,
  CSRF_HEADER_NAME,
  getAdminSession,
  isSameOriginApi,
} from '@shared/api/authSession';
import { joinApiPath } from '../../../../shared/web-api-routing';
export { joinApiPath } from '../../../../shared/web-api-routing';

type ApiBaseWindow = Window & { __OMNIVOICE_API_BASE__?: string };

export function resolveApiBase(webDeployment: boolean, dev: boolean, win?: ApiBaseWindow): string {
  if (!webDeployment || dev) return '/api';
  const runtime = win?.__OMNIVOICE_API_BASE__?.trim();
  return runtime ? runtime.replace(/\/+$/, '') : '';
}

export const API_BASE = resolveApiBase(
  __WEB_DEPLOYMENT__,
  import.meta.env.DEV,
  typeof window === 'undefined' ? undefined : (window as ApiBaseWindow),
);

export function absoluteApiBase(): string {
  if (typeof window === 'undefined') return API_BASE;
  return new URL(API_BASE || '/', window.location.href).toString().replace(/\/+$/, '');
}

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
  // Do not collapse repeated-looking segments: a reverse proxy base such as
  // `/studio` or `/api` is a transport prefix, while `/api/settings` is the
  // backend's logical route after that prefix is stripped.
  return joinApiPath(API_BASE, path);
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
  const localized = languageRejectionMessage(detail, tr) || generationFailureMessage(detail, tr);
  if (localized) return localized;
  if (
    detail &&
    typeof detail === 'object' &&
    'code' in detail &&
    detail.code === 'argos_runtime_unavailable'
  )
    return tr('engines.argosRuntimeUnavailable');
  if (
    detail &&
    typeof detail === 'object' &&
    'code' in detail &&
    detail.code === 'dub_background_unavailable'
  )
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
  const detail =
    generationFailureMessage(payload, tr) ||
    (payload && 'detail' in payload
      ? detailToString(payload.detail)
      : payload && typeof payload.error === 'string'
        ? payload.error.trim()
        : payload && typeof payload.message === 'string'
          ? payload.message.trim()
          : text.trim());
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
  let sentSession: ReturnType<typeof getAdminSession> = null;
  try {
    if (__WEB_DEPLOYMENT__) {
      const apiBase = absoluteApiBase();
      const headers = new Headers(init?.headers);
      let pin: string | null = null;
      try {
        pin = sessionStorage.getItem('ov_pin');
      } catch {
        // Cookie and bearer-session authentication still work when storage is blocked.
      }
      sentSession = getAdminSession(apiBase);
      if (pin) headers.set('X-OmniVoice-Pin', pin);
      if (sentSession) headers.set('Authorization', `Bearer ${sentSession.token}`);
      if (isSameOriginApi(apiBase)) headers.set(CSRF_HEADER_NAME, '1');
      init = { ...init, headers, credentials: 'include' };
    }
    res = await fetch(apiPath(path), init);
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw new ApiError(0, tr('tts_errors.backend_unreachable'));
  }
  // Any HTTP response, including an error status, proves the backend answered.
  recordBackendContact();
  if (!res.ok) {
    const error = await errorFromResponse(res);
    const detail = error.detail.toLowerCase();
    const adminGate = error.status === 403 && detail.includes('admin api key');
    if (__WEB_DEPLOYMENT__ && (error.status === 401 || adminGate)) {
      const mode = detail.includes('api key') ? 'apikey' : 'pin';
      const currentSession = getAdminSession(absoluteApiBase());
      const staleResponse = mode === 'apikey' && currentSession?.token !== sentSession?.token;
      if (!staleResponse) {
        if (mode === 'apikey' && sentSession) clearAdminSession();
        window.dispatchEvent(new CustomEvent('ov:auth-required', { detail: { mode } }));
      }
    }
    throw error;
  }
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

/**
 * Playable URL for a saved voice's reference clip. Pass the profile's
 * `audio_url` when available: its version token changes whenever the clip is
 * replaced, so players and HTTP caches never keep the previous sample.
 */
export function profileAudioUrl(id: string, audioUrl?: string | null): string {
  if (audioUrl?.startsWith('/profiles/')) return apiPath(audioUrl);
  return `${API_BASE}/profiles/${encodeURIComponent(id)}/audio`;
}
