/**
 * Browser-side boundary for the remote administrator credential.
 *
 * The configured master key is accepted only as an input to `exchangeApiKey`.
 * It is never written to storage and never placed in a WebSocket URL. Browser
 * clients retain only a backend-bound, short-lived session in sessionStorage;
 * same-origin clients use an HttpOnly cookie that JavaScript cannot read.
 */

export const LEGACY_API_KEY_STORAGE_KEY = 'ov_api_key';
export const ADMIN_SESSION_STORAGE_KEY = 'ov_admin_session';
export const CSRF_HEADER_NAME = 'X-VoiceStudio-CSRF';

const ADMIN_SESSION_RE = /^ovs_admin_session_[A-Za-z0-9_-]{43}$/;
const WS_TICKET_RE = /^ovs_ws_ticket_[A-Za-z0-9_-]{43}$/;
const MAX_AUTH_RESPONSE_BYTES = 16 * 1024;
const MAX_SESSION_LIFETIME_SECONDS = 9 * 60 * 60;
const MAX_TICKET_LIFETIME_SECONDS = 60;

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

type AuthWindow = {
  location?: { origin?: string };
  dispatchEvent?: (event: Event) => boolean;
  __TAURI__?: unknown;
  __TAURI_INTERNALS__?: unknown;
};

type CommonOptions = {
  apiBase: string;
  fetchImpl?: typeof fetch;
  storage?: StorageLike | null;
  windowLike?: AuthWindow;
  now?: () => number;
  timeoutMs?: number;
};

export type StoredAdminSession = {
  token: string;
  expiresAt: number;
  apiBase: string;
};

export class AuthSessionError extends Error {
  status?: number;

  constructor(status?: number) {
    super('Remote administrator authentication failed.');
    this.name = 'AuthSessionError';
    this.status = status;
  }
}

function defaultWindow(): AuthWindow | undefined {
  return typeof window === 'undefined' ? undefined : window;
}

function defaultSessionStorage(): StorageLike | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

function defaultLocalStorage(): StorageLike | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

function normalizedApiBase(raw: string): string {
  const candidate = raw.trim();
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    throw new AuthSessionError();
  }
  if (
    (url.protocol !== 'http:' && url.protocol !== 'https:') ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new AuthSessionError();
  }
  return url.toString().replace(/\/+$/, '');
}

export function isSameOriginApi(
  apiBase: string,
  windowLike: AuthWindow | undefined = defaultWindow(),
): boolean {
  try {
    const apiOrigin = new URL(normalizedApiBase(apiBase)).origin;
    const pageOrigin = windowLike?.location?.origin;
    return Boolean(pageOrigin && pageOrigin !== 'null' && apiOrigin === pageOrigin);
  } catch {
    return false;
  }
}

function removeLegacyMaster(storage: StorageLike | null = defaultLocalStorage()): void {
  try {
    storage?.removeItem(LEGACY_API_KEY_STORAGE_KEY);
  } catch {
    // A blocked storage API is already equivalent to the key not persisting.
  }
}

export function clearAdminSession({
  storage = defaultSessionStorage(),
}: { storage?: StorageLike | null } = {}): void {
  try {
    storage?.removeItem(ADMIN_SESSION_STORAGE_KEY);
  } catch {
    // Best effort; callers still stop using the in-memory value immediately.
  }
}

export function getAdminSession(
  apiBase: string,
  {
    storage = defaultSessionStorage(),
    now = Date.now,
  }: { storage?: StorageLike | null; now?: () => number } = {},
): StoredAdminSession | null {
  let normalized: string;
  try {
    normalized = normalizedApiBase(apiBase);
  } catch {
    clearAdminSession({ storage });
    return null;
  }

  let raw: string | null = null;
  try {
    raw = storage?.getItem(ADMIN_SESSION_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
  if (!raw || raw.length > 4096) {
    if (raw) clearAdminSession({ storage });
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<StoredAdminSession>;
    const nowSeconds = now() / 1000;
    if (
      !ADMIN_SESSION_RE.test(String(parsed.token ?? '')) ||
      typeof parsed.expiresAt !== 'number' ||
      !Number.isFinite(parsed.expiresAt) ||
      parsed.expiresAt <= nowSeconds ||
      parsed.expiresAt > nowSeconds + MAX_SESSION_LIFETIME_SECONDS ||
      parsed.apiBase !== normalized
    ) {
      clearAdminSession({ storage });
      return null;
    }
    return {
      token: parsed.token as string,
      expiresAt: parsed.expiresAt,
      apiBase: normalized,
    };
  } catch {
    clearAdminSession({ storage });
    return null;
  }
}

async function readBoundedText(response: Response): Promise<string> {
  const advertisedBytes = Number(response.headers?.get?.('content-length'));
  if (Number.isFinite(advertisedBytes) && advertisedBytes > MAX_AUTH_RESPONSE_BYTES) {
    throw new AuthSessionError(response.status);
  }

  const reader = response.body?.getReader();
  if (!reader) return '';
  const decoder = new TextDecoder();
  const parts: string[] = [];
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_AUTH_RESPONSE_BYTES) {
        await reader.cancel();
        throw new AuthSessionError(response.status);
      }
      parts.push(decoder.decode(value, { stream: true }));
    }
    parts.push(decoder.decode());
    return parts.join('');
  } finally {
    reader.releaseLock();
  }
}

async function readBoundedObject(response: Response): Promise<Record<string, unknown>> {
  const text = await readBoundedText(response);
  try {
    const value = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError();
    return value as Record<string, unknown>;
  } catch (error) {
    if (error instanceof AuthSessionError) throw error;
    throw new AuthSessionError(response.status);
  }
}

function plausibleExpiry(
  value: unknown,
  nowMs: number,
  maxLifetimeSeconds: number,
): value is number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return false;
  const nowSeconds = nowMs / 1000;
  return value > nowSeconds && value <= nowSeconds + maxLifetimeSeconds;
}

function responseExpiry(
  payload: Record<string, unknown>,
  nowMs: number,
  maxLifetimeSeconds: number,
): number | null {
  const relative = payload.expires_in;
  if (relative !== undefined) {
    if (
      typeof relative !== 'number' ||
      !Number.isFinite(relative) ||
      relative <= 0 ||
      relative > maxLifetimeSeconds
    ) {
      return null;
    }
    return nowMs / 1000 + relative;
  }
  return plausibleExpiry(payload.expires_at, nowMs, maxLifetimeSeconds) ? payload.expires_at : null;
}

function dispatchAuthRequired(windowLike: AuthWindow | undefined): void {
  try {
    windowLike?.dispatchEvent?.(
      new CustomEvent('ov:auth-required', { detail: { mode: 'apikey' } }),
    );
  } catch {
    // Non-browser callers can still handle the typed error.
  }
}

export async function exchangeApiKey(
  apiKey: string,
  {
    apiBase,
    fetchImpl = fetch,
    storage = defaultSessionStorage(),
    windowLike = defaultWindow(),
    now = Date.now,
    legacyStorage = defaultLocalStorage(),
    timeoutMs = 10_000,
  }: CommonOptions & { legacyStorage?: StorageLike | null },
): Promise<{ transport: 'cookie' } | { transport: 'bearer'; expiresAt: number }> {
  // A stale session must not outlive a new exchange attempt, but the
  // historical durable master is deleted only after the backend ACCEPTS the
  // exchange. Deleting it up front stranded remote-backend users whose box was
  // unreachable at first launch after upgrade: the failed exchange consumed
  // their only stored copy of OMNIVOICE_API_KEY. Keeping it on failure lets
  // the next launch retry the migration; every success path below removes it,
  // so the key never coexists with a live session.
  clearAdminSession({ storage });

  const master = apiKey.trim();
  if (!master || master.length > 8192) throw new AuthSessionError();
  const base = normalizedApiBase(apiBase);
  const transport = isSameOriginApi(base, windowLike) ? 'cookie' : 'bearer';

  let response: Response;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1, Math.min(timeoutMs, 60_000)));
  try {
    response = await fetchImpl(`${base}/api/auth/session`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${master}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ transport }),
      credentials: 'include',
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: controller.signal,
    });
  } catch {
    throw new AuthSessionError();
  } finally {
    clearTimeout(timer);
  }

  if (transport === 'cookie') {
    if (response.status !== 204) throw new AuthSessionError(response.status);
    removeLegacyMaster(legacyStorage);
    return { transport };
  }
  if (response.status !== 201) throw new AuthSessionError(response.status);

  const payload = await readBoundedObject(response);
  const token = payload.token;
  const expiresAt = responseExpiry(payload, now(), MAX_SESSION_LIFETIME_SECONDS);
  if (typeof token !== 'string' || !ADMIN_SESSION_RE.test(token) || expiresAt === null) {
    throw new AuthSessionError(response.status);
  }

  const record: StoredAdminSession = { token, expiresAt, apiBase: base };
  try {
    if (!storage) throw new TypeError();
    storage.setItem(ADMIN_SESSION_STORAGE_KEY, JSON.stringify(record));
  } catch {
    clearAdminSession({ storage });
    throw new AuthSessionError();
  }
  removeLegacyMaster(legacyStorage);
  return { transport, expiresAt };
}

/** Best-effort server revocation used when switching away from a backend.
 * Local state is cleared before the network await, so a hung or unreachable
 * backend cannot prolong the browser's ability to use the session. */
export async function revokeAdminSession(
  apiBase: string,
  {
    fetchImpl = fetch,
    storage = defaultSessionStorage(),
    windowLike = defaultWindow(),
    now = Date.now,
    timeoutMs = 1500,
  }: Omit<CommonOptions, 'apiBase'> = {},
): Promise<boolean> {
  let base: string;
  try {
    base = normalizedApiBase(apiBase);
  } catch {
    clearAdminSession({ storage });
    return false;
  }
  const session = getAdminSession(base, { storage, now });
  const sameOrigin = isSameOriginApi(base, windowLike);
  clearAdminSession({ storage });
  // Cross-origin cookie auth cannot work (the cookie is SameSite=Strict), and
  // without a bearer token there is nothing meaningful to revoke remotely.
  if (!session && !sameOrigin) return true;

  const headers: Record<string, string> = {};
  if (session) headers.Authorization = `Bearer ${session.token}`;
  if (sameOrigin) headers[CSRF_HEADER_NAME] = '1';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1, Math.min(timeoutMs, 10_000)));
  try {
    const response = await fetchImpl(`${base}/api/auth/session`, {
      method: 'DELETE',
      headers,
      credentials: 'include',
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: controller.signal,
    });
    return response.status === 204;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// Mirrors the backend ticket allowlist (`_ALLOWED_WS_PATHS` in
// backend/services/admin_sessions.py). A path listed here but not there mints
// a 422, and the consumer fails silently — keep the two in lockstep.
const ALLOWED_WS_PATHS = new Set(['/ws/events', '/ws/transcribe', '/ws/tts']);
const LOGICAL_WS_ORIGIN = 'http://omnivoice.invalid';

function websocketTarget(path: string, apiBase: string): { url: URL; logicalPath: string } {
  const base = normalizedApiBase(apiBase);
  const baseUrl = new URL(base);
  let logical: URL;
  try {
    if (!path.startsWith('/') || path.startsWith('//')) throw new TypeError();
    logical = new URL(path, `${LOGICAL_WS_ORIGIN}/`);
  } catch {
    throw new AuthSessionError();
  }
  if (logical.origin !== LOGICAL_WS_ORIGIN || !ALLOWED_WS_PATHS.has(logical.pathname)) {
    throw new AuthSessionError();
  }

  // Resolve relative to `${base}/`, not the origin root. Reverse proxies may
  // publish the backend under a path prefix (for example `/studio`). The
  // server still receives the logical route after the proxy strips its prefix,
  // so ticket binding uses `logical.pathname` below.
  const url = new URL(path.slice(1), `${base}/`);
  if (url.origin !== baseUrl.origin) throw new AuthSessionError();
  url.username = '';
  url.password = '';
  url.hash = '';
  url.searchParams.delete('api_key');
  url.searchParams.delete('ws_ticket');
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return { url, logicalPath: logical.pathname };
}

export async function requestWebSocketTicket(
  path: string,
  {
    apiBase,
    fetchImpl = fetch,
    storage = defaultSessionStorage(),
    windowLike = defaultWindow(),
    now = Date.now,
    timeoutMs = 5000,
  }: CommonOptions,
): Promise<string> {
  const base = normalizedApiBase(apiBase);
  const { logicalPath } = websocketTarget(path, base);
  const session = getAdminSession(base, { storage, now });
  if (!session) throw new AuthSessionError(401);
  // Deliberately no plaintext (`ws:`) refusal: the documented remote-GPU setup
  // is plain HTTP over a Tailscale/WireGuard tailnet (docs/remote-gpu.md), and
  // the bearer session that mints this ticket already crossed that same
  // transport. A one-use, 30 s, path-bound ticket adds no exposure the session
  // lacks; refusing it would only cut /ws/events and /ws/transcribe off for
  // every remote-backend user.

  let response: Response;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1, Math.min(timeoutMs, 30_000)));
  try {
    response = await fetchImpl(`${base}/api/auth/ws-ticket`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${session.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ path: logicalPath }),
      credentials: 'include',
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: controller.signal,
    });
  } catch {
    throw new AuthSessionError();
  } finally {
    clearTimeout(timer);
  }

  if (response.status !== 201) {
    if (response.status === 401 || response.status === 403) {
      clearAdminSession({ storage });
      dispatchAuthRequired(windowLike);
    }
    throw new AuthSessionError(response.status);
  }
  const payload = await readBoundedObject(response);
  const expiresAt = responseExpiry(payload, now(), MAX_TICKET_LIFETIME_SECONDS);
  if (
    typeof payload.ticket !== 'string' ||
    !WS_TICKET_RE.test(payload.ticket) ||
    expiresAt === null
  ) {
    throw new AuthSessionError(response.status);
  }
  return payload.ticket;
}

export async function authenticatedWsUrl(path: string, options: CommonOptions): Promise<string> {
  const { url } = websocketTarget(path, options.apiBase);
  const session = getAdminSession(options.apiBase, {
    storage: options.storage,
    now: options.now,
  });
  if (!session) return url.toString();

  const ticket = await requestWebSocketTicket(path, options);
  url.searchParams.set('ws_ticket', ticket);
  return url.toString();
}
