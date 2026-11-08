/**
 * Frontend analytics: consent-gated, content-free, nothing autocaptured.
 *
 * The interface never talks to the analytics service itself. After the user has
 * said yes, it hands a small allowlisted event to the local backend, which owns
 * the destination (config/analytics.json), the random installation profile and
 * the opt-in check, and forwards it. Before consent, or after it is withdrawn,
 * every call here is a no-op.
 *
 * What is sent is a small set of deliberate events with metadata-only
 * properties, filtered through the same allowlist the backend uses, so no
 * future caller can leak the text of a take, a voice name or a file name by
 * adding a field. There is no DOM autocapture, no session recording and no
 * stack trace: a renderer failure is reduced to a coarse class and a stage.
 */

/** Whether the interface may report at all; the backend's answer decides. */
export function analyticsAvailable(): boolean {
  return true;
}

/** The ONLY property keys allowed to leave. Mirrors backend `_ALLOWED_PROPS`.
 *  A key not on this list is dropped — not trusted. */
const ALLOWED_PROPS = new Set([
  'feature',
  'action',
  'outcome',
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

export type AnalyticsSender = (body: {
  event: string;
  props: Record<string, unknown>;
  screen?: string;
}) => Promise<unknown>;

let sender: AnalyticsSender | null = null;
let consentGeneration = 0;
let captureState: 'unresolved' | 'enabled' | 'disabled' = 'unresolved';
let lastScreen: string | null = null;

/** The consent runtime registers how events reach the local backend. */
export function setAnalyticsSender(next: AnalyticsSender | null): void {
  sender = next;
}

export const ANALYTICS_EXCEPTION_STAGES = [
  'renderer:uncaught',
  'renderer:rejection',
  'renderer:react-boundary',
  'renderer:task',
] as const;

export type AnalyticsExceptionStage = (typeof ANALYTICS_EXCEPTION_STAGES)[number];

const SAFE_ERROR_CLASSES = new Set([
  'Error',
  'TypeError',
  'RangeError',
  'ReferenceError',
  'SyntaxError',
  'URIError',
  'EvalError',
  'AggregateError',
]);
const MAX_PENDING_EXCEPTIONS = 10;
const pendingExceptions: Array<{ error_class: string; stage: AnalyticsExceptionStage }> = [];

/** Drop anything that isn't explicitly allowed. Pure + exported for tests: this
 *  is what stops a take's text, a file path, or a voice name from ever going out. */
export function sanitizeProps(props?: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props ?? {})) {
    if (!ALLOWED_PROPS.has(k)) continue;
    if (typeof v === 'string' && v.length > MAX_STR_LEN) continue;
    if (k === 'engine_id' && !(typeof v === 'string' && /^[a-z0-9][a-z0-9_.-]{0,39}$/.test(v)))
      continue;
    if (typeof v === 'number' && !Number.isFinite(v)) continue;
    if (['string', 'number', 'boolean'].includes(typeof v)) out[k] = v;
  }
  return out;
}

function send(event: string, props: Record<string, unknown>, screen?: string): void {
  if (!sender) return;
  void sender({ event, props: sanitizeProps(props), ...(screen ? { screen } : {}) }).catch(() => {
    /* Analytics never interrupts the app. */
  });
}

/** Start analytics. Call ONLY when the user has opted in. Idempotent. */
export async function enableAnalytics(): Promise<void> {
  if (captureState === 'enabled') return;
  ++consentGeneration;
  captureState = 'enabled';
  for (const pending of pendingExceptions.splice(0)) send('renderer_error', pending);
}

/** Stop analytics. Safe to call when never started. */
export function disableAnalytics(): void {
  ++consentGeneration;
  captureState = 'disabled';
  pendingExceptions.length = 0;
  lastScreen = null;
}

/** Record one event. A no-op unless the user opted in. Never throws. */
export function capture(event: string, props?: Record<string, unknown>): void {
  try {
    if (captureState !== 'enabled' || event === 'pageview' || event === 'identify') return;
    send(event, props ?? {});
  } catch {
    /* Analytics cannot interrupt the app. */
  }
}

/** Report a fixed app screen (``view:...``). Dynamic route segments and the
 *  renderer's localhost URL never leave the machine; the backend re-checks it. */
export function capturePageview(screen: string): void {
  try {
    if (captureState !== 'enabled' || screen === lastScreen) return;
    lastScreen = screen;
    send('pageview', {}, screen);
  } catch {
    /* Analytics cannot interrupt the app. */
  }
}

/** A renderer failure reduced to a coarse class and stage. No message, no stack. */
export function captureException(error: unknown, stage: AnalyticsExceptionStage): void {
  try {
    if (!ANALYTICS_EXCEPTION_STAGES.includes(stage)) return;
    const name = error instanceof Error ? error.name : 'Error';
    const error_class = SAFE_ERROR_CLASSES.has(name) ? name : 'Error';
    if (captureState === 'enabled') send('renderer_error', { error_class, stage });
    else if (captureState === 'unresolved') {
      if (pendingExceptions.length >= MAX_PENDING_EXCEPTIONS) pendingExceptions.shift();
      pendingExceptions.push({ error_class, stage });
    }
  } catch {
    /* Error reporting must never become another renderer error. */
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
