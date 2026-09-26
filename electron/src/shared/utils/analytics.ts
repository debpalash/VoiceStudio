/**
 * Frontend analytics (posthog-js) — consent-gated, autocapture OFF.
 *
 * The usual integration is one line at module load:
 *
 *     posthog.init(TOKEN, { api_host, defaults: '2026-05-30' })
 *
 * That is wrong for this app, twice over, and the reasons are not theoretical:
 *
 * 1. **It would track everyone immediately, before consent.** VoiceStudio's whole
 *    promise is that a default install sends nothing (Settings → Privacy is OFF
 *    until you turn it on; see backend/core/analytics.py). Initialising at load
 *    would make the app's own README a lie. So init happens ONLY after the user
 *    has opted in, and never before.
 *
 * 2. **posthog-js autocaptures DOM interactions by default** — clicks, form
 *    interactions, and the *text content* of the elements involved. In this app
 *    the DOM holds the script the user is about to synthesise, their voice names,
 *    and their file names. Autocapture would exfiltrate precisely the content we
 *    promise never leaves the machine. It is explicitly disabled, along with
 *    session recording (which would record the screen) and pageview capture.
 *
 * What we send instead is a small set of deliberate events with metadata-only
 * properties, filtered through the same allowlist the backend uses — so no
 * future caller can leak content by adding a field.
 *
 * The project token is a *publishable* key (PostHog's client tokens are designed
 * to ship in client code); it grants write-only event ingestion, not data access.
 */
import { scrubText } from './scrub';

/**
 * In-repo default destination (owner-sanctioned reversal, #1193): source builds
 * get the SAME consent-gated analytics as installers. This is a PostHog
 * *publishable* client key — write-only event ingestion, no data access;
 * PostHog's own FAQ says these are designed to ship in client code — NOT a
 * secret. It only names a destination: nothing is ever sent without the user's
 * explicit opt-in (init happens only after consent, see enableAnalytics()).
 * A build-time VITE_POSTHOG_KEY (release builds; developers pointing at their
 * own project) always wins over it — mirrors backend/core/analytics.py.
 * tests/test_no_committed_analytics_token.py pins that a `phc_` literal may
 * live in exactly this file and backend/core/analytics.py.
 */
const PUBLIC_PROJECT_TOKEN = 'phc_kJK9nFqtwFmB4CYWS4bMDTY7z9P4KCRyiRT7CzJTXm9f'; // gitleaks:allow — publishable write-only key (#1193)
const POSTHOG_TOKEN: string = (import.meta.env?.VITE_POSTHOG_KEY as string) || PUBLIC_PROJECT_TOKEN;
const POSTHOG_HOST: string =
  (import.meta.env?.VITE_POSTHOG_HOST as string) || 'https://us.i.posthog.com';

/** Whether this build has an analytics destination at all. */
export function analyticsAvailable(): boolean {
  return Boolean(POSTHOG_TOKEN);
}

/** The ONLY property keys allowed to leave. Mirrors backend `_ALLOWED_PROPS`.
 *  A key not on this list is dropped — not trusted. */
const ALLOWED_PROPS = new Set([
  'engine_id',
  'language',
  'mode',
  'kind',
  'source',
  'input_type',
  'effect_preset',
  'error_type',
  'duration_seconds',
  'gen_time_seconds',
  'text_length',
  'has_profile',
  'stream',
  'app_version',
  'platform',
  // Lifecycle events (backend-emitted; mirrored here so the lists stay equal —
  // pinned by tests/test_analytics_optin.py::test_frontend_allowlist_mirrors_backend).
  'from_version',
  'to_version',
  'exit_kind',
  'uptime_bucket',
  'error_class',
  'stage',
  'install_channel', // installer | docker | source — closed set, never a path
]);

/** A string longer than this is refused outright, so free text can't ride in on
 *  an allowlisted key. */
const MAX_STR_LEN = 64;

interface AnalyticsClient {
  capture(event: string, props?: Record<string, unknown>): unknown;
  captureException(error: unknown, props?: Record<string, unknown>): unknown;
  has_opted_out_capturing(): boolean;
  opt_in_capturing(): void;
  opt_out_capturing(): void;
  reset(): void;
}

const APP_ANALYTICS_ORIGIN = 'https://app.voicestudio.sh';
const SAFE_SCREEN =
  /^view:(?:home|clone|personas|stories|dub|batch|gallery|transcriptions|design|audiobook|projects|tools|calls|integrations|pro|other|settings\/(?:appearance|general|models|logs|media|pronunciation|network|sharing|credentials|performance|usage|workers|privacy|permissions|storage|support|updates|openapi|diagnostics|other))$/;
const URL_PROPERTIES = [
  '$current_url',
  '$pathname',
  '$host',
  '$title',
  '$referrer',
  '$referring_domain',
  '$initial_current_url',
  '$initial_pathname',
  '$initial_referrer',
  '$initial_referring_domain',
] as const;

interface OutgoingAnalyticsEvent {
  event?: string;
  properties?: Record<string, unknown>;
}

/** Remove renderer URLs and document metadata the SDK adds automatically. */
export function sanitizeOutgoingEvent<T extends OutgoingAnalyticsEvent>(
  payload: T | null,
): T | null {
  if (!payload) return null;
  const properties = { ...payload.properties };
  for (const key of URL_PROPERTIES) delete properties[key];
  if (payload.event === '$pageview') {
    const candidate = payload.properties?.$pathname;
    const pathname = typeof candidate === 'string' ? candidate : '';
    if (/^\/(?:[a-z-]+|settings\/[a-z-]+)$/.test(pathname)) {
      properties.$current_url = `${APP_ANALYTICS_ORIGIN}${pathname}`;
      properties.$host = 'app.voicestudio.sh';
      properties.$pathname = pathname;
    }
  }
  return { ...payload, properties };
}

let client: AnalyticsClient | null = null;
let consentGeneration = 0;
let captureState: 'unresolved' | 'enabled' | 'disabled' = 'unresolved';

export const ANALYTICS_EXCEPTION_STAGES = [
  'renderer:uncaught',
  'renderer:rejection',
  'renderer:react-boundary',
  'renderer:task',
] as const;

export type AnalyticsExceptionStage = (typeof ANALYTICS_EXCEPTION_STAGES)[number];

const MAX_PENDING_EXCEPTIONS = 10;
const MAX_STACK_LINES = 40;
const MAX_STACK_LINE_LENGTH = 512;
const pendingExceptions: Array<{ error: Error; stage: AnalyticsExceptionStage }> = [];

function safeErrorClass(error: unknown): string {
  const candidate = error instanceof Error ? error.name : 'Error';
  return /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(candidate) ? candidate : 'Error';
}

/** Build the useful part of a stack without sending messages, credentials or home paths. */
export function sanitizeException(error: unknown): Error {
  const errorClass = safeErrorClass(error);
  const sourceStack = error instanceof Error && typeof error.stack === 'string' ? error.stack : '';
  const frames = scrubText(sourceStack)
    .split('\n')
    .slice(1)
    .filter((line) =>
      /^\s*at (?:[\w$.[\]<>]+ )?\(?(?:https?:\/\/|file:\/\/|app:\/\/|~?\/)[^()\s]+:\d+:\d+\)?$/.test(
        line,
      ),
    )
    .slice(0, MAX_STACK_LINES)
    .map((line) => line.slice(0, MAX_STACK_LINE_LENGTH));
  const safe = new Error('VoiceStudio renderer error');
  safe.name = errorClass;
  safe.stack = [`${errorClass}: VoiceStudio renderer error`, ...frames].join('\n');
  return safe;
}

function sendException(error: Error, stage: AnalyticsExceptionStage): void {
  client?.captureException(error, {
    stage,
    error_class: error.name,
  });
}

/** Queue a sanitized renderer failure until the existing consent gate resolves. */
export function captureException(error: unknown, stage: AnalyticsExceptionStage): void {
  try {
    const safe = sanitizeException(error);
    if (captureState === 'enabled' && client && !client.has_opted_out_capturing()) {
      sendException(safe, stage);
      return;
    }
    if (captureState === 'disabled') return;
    if (pendingExceptions.length >= MAX_PENDING_EXCEPTIONS) pendingExceptions.shift();
    pendingExceptions.push({ error: safe, stage });
  } catch {
    /* Error reporting must never become another renderer error. */
  }
}

/** Drop anything that isn't explicitly allowed. Pure + exported for tests: this
 *  is what stops a take's text, a file path, or a voice name from ever going out. */
export function sanitizeProps(props?: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props ?? {})) {
    if (!ALLOWED_PROPS.has(k)) continue;
    if (typeof v === 'string' && v.length > MAX_STR_LEN) continue;
    if (v === null || ['string', 'number', 'boolean'].includes(typeof v)) out[k] = v;
  }
  return out;
}

/** Config that makes the promises above true rather than aspirational. */
export function hardenedConfig() {
  return {
    api_host: POSTHOG_HOST,
    // The DOM of this app contains the user's script text, voice names and file
    // names. Autocapture would send them. Never enable.
    autocapture: false,
    // Would record the screen. Never enable.
    disable_session_recording: true,
    // Renderer failures are captured manually only after redaction.
    capture_exceptions: false,
    // We send deliberate events; we don't need URL/pageview streams.
    capture_pageview: false,
    capture_pageleave: false,
    // Defence in depth: even if a recording were somehow switched on upstream,
    // don't ship text or element attributes.
    mask_all_text: true,
    mask_all_element_attributes: true,
    // Consent is the gate — never start capturing on init.
    opt_out_capturing_by_default: true,
    persistence: 'localStorage' as const,
    before_send: sanitizeOutgoingEvent,
  };
}

/** Start analytics. Call ONLY when the user has opted in. Idempotent. */
export async function enableAnalytics(): Promise<void> {
  const generation = ++consentGeneration;
  try {
    if (!POSTHOG_TOKEN) return; // no destination in this build — nothing to start
    if (!client) {
      // Electron blocks runtime extension downloads. Bundle only the error
      // tracking extension; DOM analytics and session replay stay absent.
      const [{ default: posthog }, { ErrorTrackingExtensions }] = await Promise.all([
        import('posthog-js/dist/module.slim.no-external'),
        import('posthog-js/dist/extension-bundles'),
      ]);
      if (generation !== consentGeneration) return;
      posthog.init(POSTHOG_TOKEN, {
        ...hardenedConfig(),
        __extensionClasses: { ...ErrorTrackingExtensions },
      });
      client = posthog;
    }
    const activeClient = client;
    if (!activeClient || generation !== consentGeneration) return;
    activeClient.opt_in_capturing();
    captureState = 'enabled';
    for (const pending of pendingExceptions.splice(0)) {
      sendException(pending.error, pending.stage);
    }
  } catch (e) {
    if (generation !== consentGeneration) return;
    captureState = 'disabled';
    pendingExceptions.length = 0;
    console.warn('[analytics] init failed (non-fatal)', e);
  }
}

/** Stop analytics and forget the local id. Safe to call when never started. */
export function disableAnalytics(): void {
  consentGeneration += 1;
  try {
    captureState = 'disabled';
    pendingExceptions.length = 0;
    client?.opt_out_capturing();
    client?.reset();
  } catch {
    /* nothing to stop */
  }
}

/** Record one event. A no-op unless the user opted in. Never throws. */
export function capture(event: string, props?: Record<string, unknown>): void {
  try {
    if (!client || client.has_opted_out_capturing()) return;
    client.capture(event, sanitizeProps(props));
  } catch (e) {
    console.warn('[analytics] capture failed (non-fatal)', e);
  }
}

/** Feed PostHog Web Analytics with a fixed app screen path. Dynamic route
 * segments and the renderer's localhost URL never leave the machine. */
export function capturePageview(screen: string): void {
  try {
    if (!client || client.has_opted_out_capturing() || !SAFE_SCREEN.test(screen)) return;
    const pathname = `/${screen.slice('view:'.length)}`;
    client.capture('$pageview', {
      $current_url: `${APP_ANALYTICS_ORIGIN}${pathname}`,
      $host: 'app.voicestudio.sh',
      $pathname: pathname,
      source: 'electron',
    });
  } catch (e) {
    console.warn('[analytics] pageview capture failed (non-fatal)', e);
  }
}

/** On app start: turn analytics on ONLY if the backend says the user opted in.
 *  Anything else — backend down, no consent, destination-less build — leaves it
 *  off. */
export async function initAnalyticsFromConsent(
  fetchState: () => Promise<{ opted_in?: boolean; available?: boolean }>,
): Promise<boolean> {
  const generation = consentGeneration;
  try {
    const s = await fetchState();
    if (generation !== consentGeneration) return false;
    if (s?.available && s?.opted_in) {
      await enableAnalytics();
      return captureState === 'enabled';
    }
  } catch {
    /* backend unreachable → stay off. Silence is not consent. */
  }
  return false;
}
