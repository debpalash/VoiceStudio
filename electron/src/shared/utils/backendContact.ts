/**
 * backendContact — when did the backend last prove it was alive? (#1164)
 *
 * Every apiFetch response — success OR HTTP error, both prove the process is
 * up and answering — records a timestamp here. When a request later dead-ends
 * on a transport failure, that timestamp turns the vague "can't reach the
 * backend" into one of two honest, different stories:
 *
 *   - it WAS answering moments ago and stopped → it almost certainly crashed
 *     or was killed mid-request (the OOM-kill shape), vs
 *   - it has never answered this session → it may never have started at all
 *     (port conflict, setup failure, wrong host).
 *
 * The distinction is exactly what a #1164-class bug report needs and what the
 * old one-size message erased. Kept in a module variable + sessionStorage so
 * a page reload during the outage doesn't forget the pre-reload contact.
 *
 * i18n note: the phrases go through i18next when it is initialized (the app,
 * vitest via test setup) and fall back to self-interpolated English when it
 * isn't (the node:test harness loads api/client.ts without the app bootstrap)
 * — a diagnostics message must never crash on the localization layer.
 */
import i18next from 'i18next';
import { deploymentMode, type DeploymentMode } from './deploymentMode.ts';

export const LS_LAST_CONTACT = 'ov_last_backend_contact';

let lastContactMs: number | null = null;

/** Note that the backend just answered (any HTTP response, any status). */
export function recordBackendContact(now: number = Date.now()): void {
  lastContactMs = now;
  try {
    sessionStorage.setItem(LS_LAST_CONTACT, String(now));
  } catch {
    /* storage unavailable (privacy mode / node tests) — module var suffices */
  }
}

/** Epoch ms of the last backend response this session, or null (never). */
export function lastBackendContact(): number | null {
  if (lastContactMs != null) return lastContactMs;
  try {
    const v = sessionStorage.getItem(LS_LAST_CONTACT);
    if (v) {
      const n = Number(v);
      if (Number.isFinite(n) && n > 0) return n;
    }
  } catch {
    /* noop */
  }
  return null;
}

/** Test hook — module state is process-global. */
export function _resetBackendContactForTests(): void {
  lastContactMs = null;
  try {
    sessionStorage.removeItem(LS_LAST_CONTACT);
  } catch {
    /* noop */
  }
}

/** Coarse "12 s" / "3 min" / "2 h" age — same scale as backendCrash.crashAge. */
export function contactAge(thenMs: number, nowMs: number = Date.now()): string {
  const s = Math.max(0, Math.round((nowMs - thenMs) / 1000));
  if (s < 90) return `${s} s`;
  const min = Math.round(s / 60);
  if (min < 90) return `${min} min`;
  return `${Math.round(min / 60)} h`;
}

// English fallbacks, kept byte-identical to en.json's backendUnreachable.*
// values — used only when i18next has not been initialized (see module doc).
const EN = {
  contact_recent:
    'It was answering {{ago}} ago and then stopped responding, and no crash was recorded — so it may still be alive but wedged on a heavy job that is holding the engine.',
  contact_never: 'It has not answered at all this session — it may never have started.',
  dev:
    "Can't reach the local VoiceStudio backend. {{contact}} In `bun run dev` the backend runs " +
    'with auto-reload, so any file change — including a save while a request was in flight — ' +
    'restarts it and drops the connection. Retry the action first; if it was a reload, it just ' +
    'works. If it keeps failing, check the terminal running `bun run dev` for a Python ' +
    'traceback or an exit banner, and omnivoice.log in your VoiceStudio data folder for the last ' +
    'thing the backend logged.',
  server:
    "Can't reach the VoiceStudio backend server. {{contact}} Check the server logs for the cause (e.g. `docker logs <container>` or `journalctl`) — and note that if Docker serves this page, the page itself can go down with the backend.",
  desktop:
    "Can't reach the local VoiceStudio backend. {{contact}} Open the crash notice if one appeared, " +
    'or Settings → Logs → Backend for the last thing it logged — "{{retry}}" restarts it, and ' +
    '"{{cleanRetry}}" rebuilds its environment if it will not come back.',
  misrouted:
    'The server answering {{url}} is not a VoiceStudio backend — it returned its own 404 page. ' +
    'API requests are landing on the wrong host: check the Backend URL in Settings → Sharing, ' +
    'or, if a reverse proxy serves this UI, its route for API paths.',
} as const;

function tr(key: string, vars: Record<string, string>, fallback: string): string {
  try {
    if (typeof i18next.t === 'function') {
      const out = i18next.t(key, { ...vars, defaultValue: fallback });
      if (typeof out === 'string' && out) return out;
    }
  } catch {
    /* uninitialized i18n — fall through to the English fallback */
  }
  return fallback.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? ''));
}

/** The honest last-contact phrase for error messages and bug reports.
 *
 * #1802/#1805: this used to end "it most likely crashed or was killed
 * mid-request" — a cause, asserted, on a path that by construction has already
 * looked for a crash and found none. `apiFetch` reaches here only after
 * `awaitBackendCrashMarker` has waited out the shell's death poll: a backend
 * that really died is reported as a crash, with its exit code and a "View
 * crash details" affordance, several branches earlier. So the one thing we
 * know when we get here is that nothing recorded a death — and on an Apple
 * Silicon machine mid-generation the far likelier story is a process alive and
 * stalled under memory pressure, not a dead one. Naming a crash sent those
 * users to Retry and Clean & Retry, which rebuilds the Python environment, for
 * a backend that needed neither.
 */
export function describeLastContact(nowMs: number = Date.now()): string {
  const last = lastBackendContact();
  if (last == null) return tr('backendUnreachable.contact_never', {}, EN.contact_never);
  return tr(
    'backendUnreachable.contact_recent',
    { ago: contactAge(last, nowMs) },
    EN.contact_recent,
  );
}

/**
 * Mode-aware give-up message for a transport failure (#1164, #1337).
 *
 * Every mode now carries the last-contact story, desktop included. It used to
 * be non-desktop only, and the desktop copy said "it may still be starting up,
 * or it stopped" — which its own captured data often contradicted: #1337 and
 * #1378 both recorded the backend answering **2 seconds** before the failure,
 * and a backend that answered 2s ago is not starting up. Saying so sent
 * desktop users off to wait and retry instead of at the forensics that would
 * have told them what happened.
 */
export function unreachableBackendMessage(
  mode?: DeploymentMode,
  nowMs: number = Date.now(),
): string {
  const m = mode ?? deploymentMode();
  const contact = describeLastContact(nowMs);
  if (m === 'desktop') {
    // The buttons this names are themselves translated (French renders
    // them as "Réessayer" / "Nettoyer et réessayer", Japanese differently
    // again), so quoting the English labels would send a non-English user
    // hunting for a button that says something else (CodeRabbit). Resolve
    // them through the same i18n layer.
    return tr(
      'backendUnreachable.desktop',
      {
        contact,
        retry: tr('bootstrap.retry', {}, 'Retry'),
        cleanRetry: tr('bootstrap.clean_retry', {}, 'Clean & Retry'),
      },
      EN.desktop,
    );
  }
  if (m === 'dev') return tr('backendUnreachable.dev', { contact }, EN.dev);
  return tr('backendUnreachable.server', { contact }, EN.server);
}

/**
 * A 404 answered by something that is not this backend (#1385): a rehosted UI
 * whose API requests land on its own static host, or a reverse proxy with no
 * route for API paths. Echoing that server's 404 page ("NOT_FOUND bom1::…")
 * sends the user chasing a page that never existed — name the routing problem
 * instead, and where to fix it.
 */
export function misroutedBackendMessage(url: string): string {
  return tr('backendUnreachable.misrouted', { url }, EN.misrouted);
}
