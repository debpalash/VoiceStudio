import { generationFailureMessage } from '../utils/generationFailureMessage.ts';
import { languageRejectionMessage } from '../utils/languageRejection.ts';
import i18n from 'i18next';
import { abortableDelay } from '../utils/abortableDelay.ts';
// Backend base URL.
//   • VITE_API_URL                → explicit override (any deploy).
//   • Tauri webview               → the local sidecar (127.0.0.1:<port>).
//   • Vite dev server (import.meta.env.DEV) → backend on :<port> (the dev
//     SPA runs on :3901 and the backend on :3900; CORS allows the dev origin).
//   • Anything else (served BY the backend itself — the LAN-share listener,
//     Docker, or a prod build) → SAME ORIGIN. That server serves both the SPA
//     and the API, so a remote device on http://<host>:<share-port> must hit
//     that same origin — NOT a hardcoded :3900, which is cross-origin (CORS)
//     and loopback-only/unreachable from another machine.
// Explicit .ts extension: tests/frontend/apiClient.test.mjs loads this module
// under `node --experimental-strip-types`, whose ESM resolver requires real
// file extensions (tsconfig has allowImportingTsExtensions for tsc).
import {
  awaitBackendCrashMarker,
  getUnacknowledgedBackendCrash,
  describeCrashExit,
  crashAge,
} from '../utils/backendCrash.ts';
import { backendLifecycleStage, type BackendLifecycle } from '../utils/backendLifecycle.ts';
import { scrubText } from '../utils/scrub.js';
import { deploymentMode } from '../utils/deploymentMode.ts';
import {
  lastBackendContact,
  misroutedBackendMessage,
  recordBackendContact,
  unreachableBackendMessage,
} from '../utils/backendContact.ts';
import {
  CSRF_HEADER_NAME,
  LEGACY_API_KEY_STORAGE_KEY,
  clearAdminSession,
  exchangeApiKey,
  getAdminSession,
  isSameOriginApi,
} from './authSession.ts';

const viteEnv = import.meta.env ?? {};
// Remote-backend settings (Wave 2.3): user-configured in Settings → Sharing.
// localStorage so the choice survives restarts; read once at module load —
// the Settings panel reloads the app on save.
export const LS_BACKEND_URL = 'ov_backend_url';
// Compatibility name used only to delete data written by older releases.
// New code must never persist the configured master credential.
export const LS_API_KEY = LEGACY_API_KEY_STORAGE_KEY;
// Pure + exported for unit testing — takes env + window so tests don't need to
// re-import the module or stub import.meta.env.
export function _resolveApiBase(env: any, win: any): string {
  const port = env?.VITE_API_PORT || '3900';
  // Explicit override, in precedence order:
  //   1. localStorage ov_backend_url — the user's explicit "Remote backend"
  //      setting (Wave 2.3). Beats everything: it's the one override a
  //      desktop user sets on purpose, per machine.
  //   2. window.__OMNIVOICE_API_BASE__ — RUNTIME global the backend injects
  //      into index.html from OMNIVOICE_PUBLIC_API_BASE. The only override that
  //      works on a prebuilt Docker image (VITE_* is inlined at build time).
  //   3. VITE_OMNIVOICE_API — the build-time var documented for Docker/proxy
  //      deploys and used by utils/apiBase.ts.
  //   4. VITE_API_URL — legacy alias.
  let stored = '';
  try {
    stored = (win && win.localStorage && win.localStorage.getItem(LS_BACKEND_URL)) || '';
  } catch {
    /* storage unavailable (privacy mode) */
  }
  const runtime =
    win && typeof win.__OMNIVOICE_API_BASE__ === 'string' ? win.__OMNIVOICE_API_BASE__ : '';
  const override = stored || runtime || env?.VITE_OMNIVOICE_API || env?.VITE_API_URL;
  if (override) return String(override).replace(/\/+$/, '');
  if (!win) return `http://127.0.0.1:${port}`;
  if (win.__TAURI__ || win.__TAURI_INTERNALS__) return `http://127.0.0.1:${port}`;
  if (env?.DEV) return `http://${win.location.hostname}:${port}`;
  return win.location.origin;
}
export const API = _resolveApiBase(viteEnv, typeof window !== 'undefined' ? window : undefined);

function sessionPin(): string | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage.getItem('ov_pin');
  } catch {
    // Cookie-authenticated and loopback requests must still work when a
    // privacy policy blocks Web Storage.
    return null;
  }
}

/** Build a ws:// or wss:// URL for a backend WebSocket endpoint.
 *
 * Scheme derives from the API base itself (NOT window.location — a Tauri
 * webview pointing at an https remote must still get wss). Credentials are
 * intentionally excluded; authenticated callers obtain a one-use ticket via
 * `authenticatedWsUrl` in authSession.ts. */
export function wsUrl(path: string): string {
  const base = API.replace(/^http/, 'ws').replace(/\/+$/, '');
  return `${base}${path.startsWith('/') ? '' : '/'}${path}`;
}

/**
 * Pull deep-link credentials out of a URL and return them plus a scrubbed URL.
 * Pure (no side effects) so it's unit-testable; the on-load block below applies
 * the effects.
 *   • ?pin=<pin>     (query)    — LAN-share QR. Returned as `pin` (session).
 *   • #api_key=<key> (fragment) — remote-backend deep link. Returned as `apiKey`
 *     for one immediate exchange. Read from the FRAGMENT because fragments
 *     aren't sent to the server, so the root secret stays out of request logs;
 *     the PIN stays in the query since the QR flow needs the server to see it.
 * A stray legacy ?api_key= in the query is scrubbed from `cleanUrl` but NOT
 * returned — reading it would resend the secret to the server on reload, the
 * very leak the fragment avoids. `scrubbed` is true when any credential param
 * was present, so the caller knows to rewrite the address bar.
 *
 * Caveat: the fragment is parsed with URLSearchParams, so a key containing `+`
 * decodes as a space — URL-encode such keys (`#api_key=a%2Bb`). Keys from the
 * documented `secrets.token_urlsafe` / hex generators don't contain `+`.
 */
export function _parseDeepLinkCredentials(href: string): {
  pin: string | null;
  apiKey: string | null;
  cleanUrl: string;
  scrubbed: boolean;
} {
  const url = new URL(href);
  const pin = url.searchParams.get('pin');
  const legacyQueryKey = url.searchParams.get('api_key');
  const hashParams = new URLSearchParams(url.hash.replace(/^#/, ''));
  const apiKey = hashParams.get('api_key');
  if (pin) url.searchParams.delete('pin');
  if (legacyQueryKey) url.searchParams.delete('api_key');
  if (apiKey) {
    hashParams.delete('api_key');
    url.hash = hashParams.toString();
  }
  return {
    pin,
    apiKey,
    cleanUrl: url.pathname + url.search + url.hash,
    scrubbed: Boolean(pin || apiKey || legacyQueryKey),
  };
}

type BootstrapWindow = {
  location: { href: string };
  history: { replaceState: (data: unknown, unused: string, url?: string | URL | null) => void };
};

/** One-shot migration seam kept injectable so ordering is regression-tested:
 * scrub the URL synchronously, read (never re-write) the durable master, then
 * perform the only request that may carry it. The durable copy is deleted only
 * after that exchange SUCCEEDS: deleting it first stranded remote-backend
 * users whose backend was unreachable at first launch after upgrade — the
 * failed exchange destroyed their only copy of the admin key. On failure the
 * key stays put so the next launch retries this migration. */
export async function _bootstrapBrowserCredentials(
  win: BootstrapWindow,
  {
    apiBase = API,
    sessionStore,
    localStore,
    exchange = exchangeApiKey,
  }: {
    apiBase?: string;
    sessionStore?: Pick<Storage, 'setItem'> | null;
    localStore?: Pick<Storage, 'getItem' | 'removeItem'> | null;
    exchange?: typeof exchangeApiKey;
  } = {},
): Promise<void> {
  if (sessionStore === undefined) {
    try {
      sessionStore = sessionStorage;
    } catch {
      sessionStore = null;
    }
  }
  if (localStore === undefined) {
    try {
      localStore = localStorage;
    } catch {
      localStore = null;
    }
  }
  const { pin, apiKey, cleanUrl, scrubbed } = _parseDeepLinkCredentials(win.location.href);
  if (scrubbed) {
    try {
      win.history.replaceState(null, '', cleanUrl);
    } catch {
      /* keep deleting retained credentials even if history is unavailable */
    }
  }

  let master = apiKey;
  try {
    const legacy = localStore?.getItem(LS_API_KEY) ?? null;
    if (!master) master = legacy;
  } catch {
    /* a fragment exchange can still proceed */
  }
  if (pin) {
    try {
      sessionStore?.setItem('ov_pin', pin);
    } catch {
      /* blocked PIN storage must not prevent the exchange */
    }
  }
  if (master) {
    // A rejected/unreachable exchange throws past this point, leaving the
    // durable key in place for the next launch's retry (the module-load catch
    // below still raises the auth gate). Only a session that actually exists
    // may consume the stored master.
    await exchange(master, { apiBase });
    try {
      localStore?.removeItem(LS_API_KEY);
    } catch {
      /* storage unavailable; exchangeApiKey performed the same best-effort deletion */
    }
  }
}

// On load, capture deep-link credentials, scrub the address bar synchronously,
// and exchange a master key exactly once. Historical durable master storage is
// read before the first await and deleted only once the exchange succeeds, so
// an unreachable backend leaves it for the next launch to retry. apiFetch
// waits for this one-shot migration so no request races ahead with an
// unauthenticated first call.
let authBootstrapPromise: Promise<void> = Promise.resolve();
if (typeof window !== 'undefined') {
  try {
    authBootstrapPromise = _bootstrapBrowserCredentials(window).catch(() => {
      window.dispatchEvent(new CustomEvent('ov:auth-required', { detail: { mode: 'apikey' } }));
    });
  } catch {
    /* noop */
  }
}

export class ApiError extends Error {
  status?: number;
  detail?: unknown;
  /**
   * The backend exception type behind an unclassified failure.
   *
   * The 500 handler puts `error_class` in the response body, but nothing
   * lifted it onto the Error — so the auto bug reporter, which reads the
   * Error, filed "VoiceStudio hit an internal error; check the backend log"
   * and nothing else. Every such report looked identical and none could be
   * triaged (#1773).
   *
   * #1956 did this for the streaming path. This is the classic path, which
   * had been carrying the datum on the wire the whole time.
   */
  errorClass?: string;
  constructor(
    message: string,
    init: { status?: number; detail?: unknown; errorClass?: string } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = init.status;
    this.detail = init.detail;
    this.errorClass =
      typeof init.errorClass === 'string' && init.errorClass ? init.errorClass : undefined;
  }
}

export function apiUrl(path?: string): string {
  if (!path) return API;
  return path.startsWith('http') ? path : `${API}${path.startsWith('/') ? '' : '/'}${path}`;
}

/** Whether an already-resolved request URL stays inside the configured API
 * origin and path prefix. Absolute URLs remain supported for public media, but
 * they must never inherit backend credentials by accident. */
export function _isApiTarget(target: string, apiBase: string = API): boolean {
  try {
    const base = new URL(apiBase.replace(/\/+$/, '') + '/');
    const url = new URL(target);
    if (url.origin !== base.origin) return false;
    const prefix = base.pathname.replace(/\/+$/, '');
    return !prefix || url.pathname === prefix || url.pathname.startsWith(`${prefix}/`);
  } catch {
    return false;
  }
}

// Stamped on EVERY response by the backend's BackendMarkerMiddleware and
// exposed cross-origin, so its presence is AUTHORITATIVE: this really is an
// VoiceStudio backend answering, whatever the body looks like (#1385).
const BACKEND_MARKER_HEADER = 'x-omnivoice-backend';

// `backendShaped` — did this response come from a VoiceStudio backend?
//
// The marker header settles it outright. Body shape is the fallback for a
// backend older than the header (a desktop app pointed at a remote box that
// hasn't been updated): every backend error route answers JSON with
// `detail`/`error`, and Starlette's mounted StaticFiles answer plain-text
// "Not Found" — the one non-JSON backend voice. That fallback is a heuristic,
// which is exactly why the header exists: a proxy CAN return
// `{"error":"Not Found"}` and impersonate the shape.
async function readError(
  res: Response,
): Promise<{ detail: unknown; backendShaped: boolean; localized?: string }> {
  const marked = Boolean(res.headers?.get?.(BACKEND_MARKER_HEADER));
  const text = await res.text().catch(() => '');
  try {
    const j = JSON.parse(text);
    // `detail` is FastAPI/Starlette's own error key — including the
    // `{"detail":"Not Found"}` an unrouted path produces. `error` is used by
    // a few 4xx/5xx handlers but never for a 404, so an unmarked
    // `{"error":…}` 404 is a foreign server, not an old backend.
    const localized =
      generationFailureMessage(j, i18n.t) || generationFailureMessage(j.detail, i18n.t);
    if (j.detail) return { detail: j.detail, backendShaped: true, localized };
    if (j.error) return { detail: j.error, backendShaped: marked };
    return { detail: text || res.statusText, backendShaped: marked };
  } catch {
    return { detail: text || res.statusText, backendShaped: marked || text.trim() === 'Not Found' };
  }
}

// Backoff (ms) for retrying a *transport-level* failure — the backend briefly
// down while the auto-restart supervisor brings it back (#567/#570/#571). One
// short cascade (~2.9 s total) so a blip becomes invisible, yet a
// genuinely-down backend still surfaces the actionable error promptly.
const TRANSPORT_RETRY_BACKOFF_MS = [400, 900, 1600];

// A REAL backend start/restart is 10–20+ s (venv python spawn + torch
// import), not 2.9 s — measured on a 16 GB M-series Mac; slower disks take
// longer. When the desktop shell says the backend is starting/restarting
// (bootstrap_status ≠ ready/failed), keep retrying at this interval instead
// of dead-ending every request mid-restart with "Can't reach the local
// VoiceStudio backend". Bounded by STARTUP_GRACE_MS (matches the supervisor's
// own 120 s respawn-health wait in src-tauri/src/bootstrap.rs); the shell
// flipping to `failed` — or being absent (browser/Docker) — exits the wait
// immediately, so a truly dead backend still errors promptly.
const RESTART_WAIT_INTERVAL_MS = 1500;
const STARTUP_GRACE_MS = 120_000;

// #1101: the shell's stage is a 2-second POLL, not a live probe. When the
// backend dies mid-generate, `supervise_backend` needs up to ~2 s to notice the
// exit, record the crash marker, and flip the stage to "starting" — so a single
// check at the end of the ~2.9 s cascade very often still sees `ready` and we
// dead-ended on the generic "Can't reach the backend" anyway. That was the hole
// in the #1094 fix, reported against 0.3.19.
//
// A transport failure CONTRADICTS `ready`: if the shell believed the backend
// were reachable, the fetch would have succeeded. So `ready` is treated as a
// STALE belief, not an authority — we keep retrying across this reconciliation
// window, re-asking each time, which lets a death the supervisor hasn't noticed
// yet turn into "starting" (→ the long wait + banner) and gives the crash marker
// time to be written so the error can tell the honest story instead of guessing.
// Only `failed` (the shell gave up) or `unknown` (no shell — browser/Docker)
// still errors immediately.
const RECONCILE_MS = 12_000;
const RECONCILE_INTERVAL_MS = 1000;

export type ApiFetchOptions = RequestInit & { retryTransport?: boolean };

export async function apiFetch(path: string, opts: ApiFetchOptions = {}): Promise<Response> {
  await authBootstrapPromise;
  const requestUrl = apiUrl(path);
  const backendTarget = _isApiTarget(requestUrl);
  const pin = backendTarget ? sessionPin() : null;
  const session = backendTarget ? getAdminSession(API) : null;
  const { retryTransport = true, ...requestOpts } = opts;
  const headers = new Headers(requestOpts.headers);
  if (pin) headers.set('X-OmniVoice-Pin', pin);
  if (session) headers.set('Authorization', `Bearer ${session.token}`);
  // Same-origin browser clients authenticate through an HttpOnly cookie. The
  // marker makes ambient-cookie mutations fail closed under the backend's
  // exact-Origin CSRF policy; browser-managed Sec-Fetch-Site supplies the
  // additional guard for side-effectful GET routes.
  if (backendTarget && isSameOriginApi(API)) headers.set(CSRF_HEADER_NAME, '1');
  const finalOpts: RequestInit = {
    ...requestOpts,
    headers,
    ...(backendTarget ? { credentials: 'include' as RequestCredentials } : {}),
  };
  const signal = finalOpts.signal as AbortSignal | null | undefined;
  let lastDetail = '';
  // The shell's last word on the backend. When it still says `ready` after we've
  // exhausted the reconcile window, the process is demonstrably ALIVE and simply
  // not answering — a different failure from "it stopped", and it deserves a
  // different sentence (#1113).
  // #1177: `.message` carries the shell's full diagnosis for a `failed` stage
  // (exit code + stderr tail, or the venv-bootstrap reason) — the evidence the
  // generic "can't reach" message used to throw away.
  let lastStage: BackendLifecycle = { stage: 'unknown', message: null };
  const startedAt = Date.now();
  for (let attempt = 0; ; attempt++) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    let res: Response;
    try {
      res = await fetch(requestUrl, finalOpts);
      // Any response — success or HTTP error alike — proves the backend
      // process is alive and answering. Recording it lets a LATER transport
      // failure say "it was answering Xs ago and stopped" instead of the
      // one-size "can't reach" (#1164).
      if (backendTarget) recordBackendContact();
    } catch (e) {
      // A thrown fetch (TypeError "Failed to fetch" / "NetworkError") means the
      // request never reached the backend — it's still starting up, crashed, or
      // the dev server dropped. The auto-restart supervisor revives it within a
      // few seconds, so retry a bounded few times with backoff before surfacing
      // the actionable ApiError, making a brief restart window invisible
      // (issues #438/#454/#466/#567). Never retry a deliberate abort. status:0
      // lets callers distinguish a transport failure from an HTTP error.
      if (signal?.aborted || (e as Error)?.name === 'AbortError') throw e;
      lastDetail = String((e as Error)?.message || e);
      if (retryTransport && attempt < TRANSPORT_RETRY_BACKOFF_MS.length) {
        await abortableDelay(TRANSPORT_RETRY_BACKOFF_MS[attempt], signal);
        continue;
      }
      // The short cascade is exhausted, but the desktop shell may KNOW the
      // backend is mid-start/restart (a real one takes 10–20+ s — torch
      // import — not 2.9 s). Keep waiting exactly as long as the shell says
      // "starting", bounded by STARTUP_GRACE_MS.
      const elapsed = Date.now() - startedAt;
      if (retryTransport && elapsed < STARTUP_GRACE_MS) {
        try {
          lastStage = await backendLifecycleStage();
        } catch {
          /* never let the lifecycle probe mask the real transport error */
        }
        const stage = lastStage.stage;
        if (stage === 'starting') {
          await abortableDelay(RESTART_WAIT_INTERVAL_MS, signal);
          continue;
        }
        // `ready` while the transport is failing is a contradiction — the
        // shell's 2 s poll simply hasn't caught up with a backend that just
        // died (#1101). Don't believe it yet: keep retrying briefly so the
        // supervisor can notice, flip to "starting", and write the crash
        // marker. 'failed'/'unknown' fall through and error now, so a shell
        // that gave up — or no shell at all — still surfaces promptly.
        if (stage === 'ready' && elapsed < RECONCILE_MS) {
          await abortableDelay(RECONCILE_INTERVAL_MS, signal);
          continue;
        }
      }
      // Diagnostics that ride on every give-up ApiError (#1164): the bug
      // report builder reads these to say WHICH deployment failed, whether
      // the backend ever answered this session, and how long we retried.
      const mode = deploymentMode();
      const failureDetail = {
        transport: lastDetail,
        mode,
        lastContactMs: lastBackendContact(),
        firstFailureTs: startedAt,
        attempts: attempt + 1,
      };
      // #941: if a backend crash was recorded — by the desktop shell's death
      // watcher, or (browser/dev/Docker) by the backend's own run sentinel —
      // tell the honest story instead of the vague "can't reach" and let
      // BackendCrashNotice raise its "View crash details" affordance.
      //
      // #1802/#1805: asking ONCE, right now, races the shell's ~2 s death
      // poll and loses — the marker for a backend that just died has usually
      // not been written yet. We then concluded "no crash" and said so, which
      // is how a give-up came to assert "it most likely crashed or was killed
      // mid-request" while carrying no forensics to back it. `streamDropError`
      // already waits out that poll (#1119); this path never did. The budget
      // is shorter than the stream path's 8 s because the retry cascade above
      // has already cost the user a few seconds — two poll intervals is enough
      // to stop losing the race without turning every failure into a stall.
      const { crash } = await awaitBackendCrashMarker(getUnacknowledgedBackendCrash, {
        waitMs: 4_000,
        intervalMs: 1_000,
        signal,
      });
      if (crash) {
        try {
          window.dispatchEvent(new CustomEvent('ov:backend-crashed', { detail: crash }));
        } catch {
          /* no window (tests) — the ApiError below still tells the story */
        }
        throw new ApiError(
          `The local VoiceStudio backend crashed (${describeCrashExit(crash)}) ${crashAge(crash)} ago ` +
            'and is being restarted — this request could not reach it. ' +
            'Open the crash notice for the error output, or check Settings → Logs → Backend.',
          { status: 0, detail: failureDetail },
        );
      }
      // #1113: no crash was recorded AND the shell still reports the backend as
      // running — so it did NOT stop; it is alive and has stopped answering.
      // Telling this user "it may still be starting up, or it stopped" is simply
      // false, and it sends them to restart the app when the real cause is a
      // wedged job holding the worker (a heavy generate/transcribe on a small
      // GPU). Name what actually happened and point at the thing that fixes it.
      if (lastStage.stage === 'ready') {
        throw new ApiError(
          'The local VoiceStudio backend is running but stopped responding. This usually means a ' +
            'job (a generation or a transcription) is stuck holding the engine — often a model ' +
            'too heavy for the available memory on this machine. Check Settings → Logs → Backend ' +
            'for the last thing it was doing; a smaller model or a lighter engine, both in Model ' +
            'Catalogue, is the usual fix. Restarting the app clears it for now.',
          { status: 0, detail: failureDetail },
        );
      }
      // #1177: the shell says the backend START FAILED — and it knows exactly
      // why. `BootstrapStage::Failed { message }` carries the exit code plus a
      // ~30-line stderr tail, or the precise reason `ensure_venv_ready`
      // refused (Intel Mac unsupported, a failed `uv sync`, a blocked GitHub).
      // That diagnosis used to die inside the shell: the lifecycle probe
      // returned only the stage tag, so EVERY start-failure mode collapsed
      // into the evidence-free "may still be starting up, or it stopped" —
      // which is also simply wrong (it is not starting, and it will not come
      // back on its own). Surface the real reason, and raise the notice so the
      // full output is one click from the "report" affordance.
      //
      // Scrubbed here rather than at the report boundary: this string is the
      // Error message, and it flows into breadcrumbs, logs and any future
      // telemetry — the home path must be gone before it is anywhere but the
      // shell (buildBugReportUrl scrubs again; belt and braces).
      if (lastStage.stage === 'failed' && lastStage.message) {
        const diagnosis = scrubText(lastStage.message).trim();
        try {
          window.dispatchEvent(
            new CustomEvent('ov:backend-start-failed', { detail: { message: diagnosis } }),
          );
        } catch {
          /* no window (tests) — the ApiError below still carries the diagnosis */
        }
        throw new ApiError(
          'The local VoiceStudio backend could not start, so this request had nowhere to go. ' +
            `The app reported:\n\n${diagnosis}\n\n` +
            'Open the details for the full output, or use Retry / Clean & Retry in Settings → Logs → Backend.',
          { status: 0, detail: { ...failureDetail, startFailure: diagnosis } },
        );
      }
      // #1164: every deployment gets forensics that exist in ITS world — the
      // old desktop-shaped copy sent dev/Docker users chasing a "restart the
      // app" they don't have.
      //
      // #1337: and desktop now gets the same honesty it was giving everyone
      // else. Its copy used to say "it may still be starting up, or it
      // stopped" regardless of what we knew — while #1337 and #1378 both
      // recorded the backend answering 2 SECONDS before the failure. A
      // backend that answered 2s ago is not starting up, and telling those
      // users to wait and retry sent them away from the crash forensics.
      throw new ApiError(unreachableBackendMessage(mode), {
        status: 0,
        detail: failureDetail,
      });
    }
    if (!res.ok) {
      // An HTTP error means the backend *did* respond — never retry it.
      const { detail, backendShaped, localized } = await readError(res);
      // #1385: a 404 in some other server's voice means the request never
      // reached a VoiceStudio backend at all — a static host's catch-all page
      // or a reverse proxy with no route for this path. Echoing that page
      // ("NOT_FOUND bom1::…") sends the user chasing a page that never
      // existed; name the actual problem instead: where requests are going.
      if (backendTarget && res.status === 404 && !backendShaped) {
        throw new ApiError(misroutedBackendMessage(requestUrl), {
          status: res.status,
          detail,
        });
      }
      // 401 on a remote device: route to the right gate by reading the detail.
      // "API key required" (BearerKeyMiddleware, OMNIVOICE_API_KEY) vs anything
      // else, i.e. "PIN required" (NetworkAccessMiddleware). Both are 401; the
      // detail is the only discriminator (only two 401 sites exist backend-side).
      // The router-level admin gates answer 403 "loopback origin or admin API
      // key required" (require_admin/require_admin_action) — same situation, the
      // client just isn't admin-authenticated — so it routes to the API-key form
      // too. Other 403s (CSRF "browser origin rejected", loopback-only routes)
      // are NOT credential gaps; presenting a key won't help, so they stay plain
      // errors.
      const adminGate403 =
        res.status === 403 &&
        typeof detail === 'string' &&
        detail.toLowerCase().includes('admin api key');
      if (backendTarget && (res.status === 401 || adminGate403) && typeof window !== 'undefined') {
        // readError's declared `string` return isn't guaranteed at runtime —
        // `j.detail` can be a structured object/array on a future 401. Match only
        // real strings (avoids both a `.toLowerCase()` crash and `String()` itself
        // throwing on a malformed object); anything else falls back to PIN.
        // (No adminGate403 arm here: "admin api key" ⊇ "api key", so the sniff
        // below already yields 'apikey' for every admin-gate 403.)
        const mode =
          typeof detail === 'string' && detail.toLowerCase().includes('api key') ? 'apikey' : 'pin';
        // A failed response may only invalidate the credentials it actually
        // carried (`session` is captured at send time). Clearing blindly let
        // a stale 403 that landed after a key exchange wipe the fresh
        // session, reloading a successful login straight back into the gate.
        const currentSession = getAdminSession(API);
        const staleAdminResponse = mode === 'apikey' && currentSession?.token !== session?.token;
        if (mode === 'apikey' && !staleAdminResponse && session) {
          clearAdminSession();
        }
        if (!staleAdminResponse) {
          window.dispatchEvent(new CustomEvent('ov:auth-required', { detail: { mode } }));
        }
      }
      // Structured details (e.g. the typed asr_model_missing 409) carry a
      // human-readable `message` — use it for the Error message instead of
      // letting the object stringify to "[object Object]".
      const msg =
        localized ||
        languageRejectionMessage(detail, i18n.t) ||
        (detail &&
        typeof detail === 'object' &&
        'code' in detail &&
        detail.code === 'argos_runtime_unavailable'
          ? i18n.t('engines.argosRuntimeUnavailable', {
              defaultValue: 'Argos runtime unavailable. Reinstall the backend or select NLLB.',
            })
          : typeof detail === 'string'
            ? detail
            : ((detail as { message?: string })?.message ?? JSON.stringify(detail)));
      // The backend names the exception type in `error_class` on its 500s.
      // Lifting it here is what lets the bug report say which failure it was.
      const errorClass =
        detail && typeof detail === 'object'
          ? (detail as { error_class?: unknown }).error_class
          : undefined;
      throw new ApiError(`${res.status} ${res.statusText}: ${msg}`, {
        status: res.status,
        detail,
        errorClass: typeof errorClass === 'string' ? errorClass : undefined,
      });
    }
    return res;
  }
}

export async function apiJson<T = unknown>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await apiFetch(path, opts);
  return res.json() as Promise<T>;
}

export async function apiPost<T = unknown>(
  path: string,
  body?: unknown,
  opts: RequestInit = {},
): Promise<T> {
  const init: RequestInit = { method: 'POST', ...opts };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers = {
      'Content-Type': 'application/json',
      ...(opts.headers as Record<string, string>),
    };
    init.body = JSON.stringify(body);
  }
  return apiJson<T>(path, init);
}

export async function apiDelete(path: string, opts: RequestInit = {}): Promise<Response> {
  return apiFetch(path, { method: 'DELETE', ...opts });
}
