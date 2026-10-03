import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

const MAX_RESPONSE_BYTES = 16 * 1024;
const MAX_SESSION_SECONDS = 9 * 60 * 60;
const SESSION_TOKEN = /^ovs_admin_session_[A-Za-z0-9_-]{43}$/;
const WS_TICKET = /^ovs_ws_ticket_[A-Za-z0-9_-]{43}$/;

export type RemoteProbeKind =
  | 'invalid'
  | 'tls'
  | 'network'
  | 'timeout'
  | 'http'
  | 'wrong_port'
  | 'auth';

export interface RemoteSession {
  token: string;
  expiresAt: number;
}

export type RemoteProbeResult =
  | { ok: true; detail: string; target: string; session: RemoteSession | null }
  | { ok: false; kind: RemoteProbeKind; status?: number; target: string };

interface ProbeOptions {
  fetcher?: typeof fetch;
  now?: () => number;
  timeoutMs?: number;
}

export function normalizeRemoteUrl(raw: string): string {
  const value = raw.trim();
  if (!value) throw new Error('A backend URL is required');
  const url = new URL(value);
  if (
    (url.protocol !== 'http:' && url.protocol !== 'https:') ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error('Invalid backend URL');
  }
  return url.toString().replace(/\/+$/, '');
}

async function readObject(response: Response): Promise<Record<string, unknown> | null> {
  const advertised = Number(response.headers.get('content-length'));
  if (Number.isFinite(advertised) && advertised > MAX_RESPONSE_BYTES) return null;
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > MAX_RESPONSE_BYTES) return null;
  try {
    const value: unknown = JSON.parse(new TextDecoder().decode(bytes));
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function transportKind(target: string, error: unknown): RemoteProbeKind {
  if (new URL(target).port === '7443') return 'wrong_port';
  const name = error instanceof Error ? error.name : '';
  if (name === 'AbortError' || name === 'TimeoutError') return 'timeout';
  const message =
    error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
  return /certificate|cert_|ssl|tls/.test(message) ? 'tls' : 'network';
}

async function fetchWithin(
  fetcher: typeof fetch,
  url: string,
  init: RequestInit,
  timeoutMs: number,
  expectedStatus?: number,
): Promise<{ ok: boolean; status: number; payload: Record<string, unknown> | null }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher(url, {
      ...init,
      cache: 'no-store',
      redirect: 'error',
      signal: controller.signal,
    });
    // Fetch resolves at headers; keep the deadline armed through JSON reads.
    const accepted =
      expectedStatus === undefined ? response.ok : response.status === expectedStatus;
    const payload = accepted ? await readObject(response) : null;
    return { ok: response.ok, status: response.status, payload };
  } finally {
    clearTimeout(timer);
  }
}

export async function probeRemoteBackend(
  rawUrl: string,
  masterKey: string,
  { fetcher = fetch, now = Date.now, timeoutMs = 5000 }: ProbeOptions = {},
): Promise<RemoteProbeResult> {
  let target: string;
  try {
    target = normalizeRemoteUrl(rawUrl);
  } catch {
    return { ok: false, kind: 'invalid', target: rawUrl.trim() };
  }

  try {
    const healthResponse = await fetchWithin(fetcher, `${target}/health`, {}, timeoutMs);
    if (!healthResponse.ok) {
      return { ok: false, kind: 'http', status: healthResponse.status, target };
    }
    const health = healthResponse.payload;
    if (
      !health ||
      health.status !== 'ok' ||
      typeof health.version !== 'string' ||
      typeof health.device !== 'string'
    ) {
      return { ok: false, kind: 'wrong_port', target };
    }

    let session: RemoteSession | null = null;
    const master = masterKey.trim();
    if (master) {
      if (master.length > 8192) return { ok: false, kind: 'auth', status: 401, target };
      const exchange = await fetchWithin(
        fetcher,
        `${target}/api/auth/session`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${master}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ transport: 'bearer' }),
        },
        timeoutMs,
        201,
      );
      if (exchange.status !== 201) {
        return {
          ok: false,
          kind: exchange.status === 401 || exchange.status === 403 ? 'auth' : 'http',
          status: exchange.status,
          target,
        };
      }
      const payload = exchange.payload;
      const token = payload?.token;
      const relative = payload?.expires_in;
      if (
        typeof token !== 'string' ||
        !SESSION_TOKEN.test(token) ||
        typeof relative !== 'number' ||
        !Number.isFinite(relative) ||
        relative <= 0 ||
        relative > MAX_SESSION_SECONDS
      ) {
        return { ok: false, kind: 'auth', status: exchange.status, target };
      }
      session = { token, expiresAt: now() / 1000 + relative };
    }

    const headers = session ? { Authorization: `Bearer ${session.token}` } : undefined;
    const infoResponse = await fetchWithin(
      fetcher,
      `${target}/system/info`,
      { headers },
      timeoutMs,
    );
    if (!infoResponse.ok) {
      return {
        ok: false,
        kind: infoResponse.status === 401 || infoResponse.status === 403 ? 'auth' : 'http',
        status: infoResponse.status,
        target,
      };
    }
    const info = infoResponse.payload;
    if (!info || typeof info.app_version !== 'string') {
      return { ok: false, kind: 'wrong_port', target };
    }
    return {
      ok: true,
      detail: `${health.version} on ${health.device}`,
      target,
      session,
    };
  } catch (error) {
    return { ok: false, kind: transportKind(target, error), target };
  }
}

export async function remoteWebSocketUrl(
  rawUrl: string,
  path: '/ws/transcribe' | '/ws/events' | '/ws/tts',
  session: RemoteSession | null,
  { fetcher = fetch, now = Date.now, timeoutMs = 5000 }: ProbeOptions = {},
): Promise<string> {
  const target = normalizeRemoteUrl(rawUrl);
  const url = new URL(path.slice(1), `${target}/`);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  if (!session) return url.toString();
  if (session.expiresAt <= now() / 1000) throw new Error('Remote session expired');
  const response = await fetchWithin(
    fetcher,
    `${target}/api/auth/ws-ticket`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${session.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ path }),
    },
    timeoutMs,
    201,
  );
  if (response.status !== 201)
    throw new Error(`Could not authorize WebSocket (HTTP ${response.status})`);
  const payload = response.payload;
  const expiresIn = payload?.expires_in;
  if (
    typeof payload?.ticket !== 'string' ||
    !WS_TICKET.test(payload.ticket) ||
    typeof expiresIn !== 'number' ||
    !Number.isFinite(expiresIn) ||
    expiresIn <= 0 ||
    expiresIn > 60
  ) {
    throw new Error('Remote backend returned an invalid WebSocket ticket');
  }
  url.searchParams.set('ws_ticket', payload.ticket);
  return url.toString();
}

/** The URL is not a credential; the short-lived bearer remains memory-only. */
export function loadRemoteBackend(path: string): string | null {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    return normalizeRemoteUrl(String((parsed as { url?: unknown }).url ?? ''));
  } catch {
    return null;
  }
}

export function saveRemoteBackend(path: string, url: string | null): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, JSON.stringify({ url }, null, 2), { encoding: 'utf8', mode: 0o600 });
  renameSync(temporary, path);
}
