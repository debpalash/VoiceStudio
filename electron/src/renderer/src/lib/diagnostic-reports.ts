import schema from '../../../../../config/diagnostics.json';
import { getBridge } from '@/components/bridge';
import { apiJson } from '@/lib/api/client';

type DiagnosticBridge = NonNullable<Window['voicestudio']['diagnostics']>;
export type DiagnosticState = Awaited<ReturnType<DiagnosticBridge['getStatus']>>;
export type DiagnosticInput = Parameters<DiagnosticBridge['capture']>[0];
export type DiagnosticCategory = DiagnosticInput['category'];
export type DiagnosticStage = NonNullable<DiagnosticInput['stage']>;
export interface RendererDiagnosticState {
  status: DiagnosticState | null;
  loading: boolean;
  saving: boolean;
  error: boolean;
}
let current: RendererDiagnosticState = {
  status: null,
  loading: false,
  saving: false,
  error: false,
};
let revision = 0;
let locallyDisabled = false;
let failedChoice: boolean | null = null;
let acknowledgedChoice = false;
let reading: Promise<boolean> | null = null;
let seenErrors = new WeakSet<object>();
const listeners = new Set<() => void>();
const deliveries = new Map<AbortController, ReturnType<typeof setTimeout>>();
const framePaths = new Set(schema.frame_paths);
const errorTypes = new Set(schema.error_types);
const errorCodes = new Set(schema.error_codes);
const expectedCodes = new Set([
  'UNKNOWN',
  'INPUT_TOO_SHORT',
  'INVALID_MEDIA_FILE',
  'NO_AUDIO_TRACK',
  'CLONE_REFERENCE_MISSING',
  'REFERENCE_STT_MISSING',
  'POCKETTTS_GATED_WEIGHTS',
  'PYANNOTE_LICENSE_REQUIRED',
  'SOURCE_LANGUAGE_REQUIRED',
  'UNSUPPORTED_VIDEO_URL',
  'VIDEO_DRM_PROTECTED',
  'HF_AUTH_FAILED',
  'HF_MIRROR_GATED',
]);

function errorCodeOf(error: unknown): string | undefined {
  if (!error || typeof error !== 'object') return;
  const record = error as Record<string, unknown>;
  const payload =
    record.payload && typeof record.payload === 'object'
      ? (record.payload as Record<string, unknown>)
      : {};
  const detail =
    payload.detail && typeof payload.detail === 'object' ? payload.detail : record.detail;
  for (const value of [record, payload, detail]) {
    if (!value || typeof value !== 'object') continue;
    for (const key of ['docs_topic', 'error_code', 'code', 'error_class']) {
      const candidate = (value as Record<string, unknown>)[key];
      if (
        typeof candidate === 'string' &&
        candidate.length <= 80 &&
        errorCodes.has(candidate.toUpperCase())
      )
        return candidate.toUpperCase();
    }
  }
}

export const getDiagnosticState = () => current;
export function subscribeDiagnostics(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
function publish(patch: Partial<RendererDiagnosticState>): void {
  current = { ...current, ...patch };
  for (const listener of listeners) listener();
}
export function diagnosticsAllowed(): boolean {
  return (
    !locallyDisabled &&
    Boolean(current.status?.enabled && current.status.opted_in && current.status.available)
  );
}
function stateFrom(value: unknown): DiagnosticState {
  if (!value || typeof value !== 'object') throw new Error('Invalid diagnostic state');
  const state = value as DiagnosticState;
  for (const key of ['enabled', 'opted_in', 'available', 'prompted'] as const)
    if (typeof state[key] !== 'boolean') throw new Error('Invalid diagnostic state');
  return state;
}

/** A failed or stale consent read cannot authorize diagnostic collection. */
export function refreshDiagnosticState(): Promise<boolean> {
  if (current.saving) return Promise.resolve(false);
  if (reading) return reading;
  const started = revision;
  publish({ loading: true });
  reading = Promise.resolve().then(async () => {
    try {
      const bridge = getBridge()?.diagnostics;
      const response = stateFrom(
        await (bridge ? bridge.getStatus() : apiJson('/api/settings/diagnostics')),
      );
      const state = {
        ...response,
        prompted: response.prompted || acknowledgedChoice,
        ...(locallyDisabled ? { enabled: false, opted_in: false } : {}),
      };
      if (started !== revision) return false;
      publish({ status: state, loading: false, error: failedChoice !== null });
      return diagnosticsAllowed();
    } catch {
      if (started === revision) publish({ status: null, loading: false, error: true });
      return false;
    } finally {
      reading = null;
    }
  });
  return reading;
}

/** Startup replay is limited to an already-enabled, unchanged consent choice. */
export function initializeDiagnosticReporting(onEnabled?: () => void): Promise<void> {
  const started = revision;
  return refreshDiagnosticState().then((allowed) => {
    if (allowed && started === revision && diagnosticsAllowed()) {
      try {
        onEnabled?.();
      } catch {
        /* Startup reporting must never create another fault. */
      }
    }
  });
}

export async function setDiagnosticConsent(enabled: boolean): Promise<DiagnosticState> {
  const started = ++revision;
  locallyDisabled = true;
  failedChoice = null;
  for (const [controller, timer] of deliveries) {
    clearTimeout(timer);
    controller.abort();
  }
  deliveries.clear();
  seenErrors = new WeakSet();
  const prior = current.status;
  // Off takes effect before any asynchronous persistence or old read resolves.
  publish({
    status: prior ? { ...prior, enabled: false, opted_in: false } : null,
    saving: true,
    loading: false,
    error: false,
  });
  try {
    const bridge = getBridge()?.diagnostics;
    const state = stateFrom(
      await (bridge
        ? bridge.setConsent(enabled)
        : apiJson('/api/settings/diagnostics', {
            method: 'PUT',
            body: JSON.stringify({ enabled }),
          })),
    );
    if (started === revision) {
      acknowledgedChoice = true;
      locallyDisabled = !enabled || !(state.enabled && state.opted_in && state.available);
      publish({
        status: locallyDisabled ? { ...state, enabled: false, opted_in: false } : state,
        saving: false,
        error: false,
      });
    }
    return state;
  } catch (error) {
    if (started === revision) {
      failedChoice = enabled;
      publish({ saving: false, error: true });
    }
    throw error;
  }
}

/** A failed withdrawal must retry its write, not accept the server's old permission. */
export async function retryDiagnosticConsent(): Promise<void> {
  if (failedChoice !== null) await setDiagnosticConsent(failedChoice);
  else await refreshDiagnosticState();
}

/** Select tracked app locations; personal paths and arbitrary asset names never leave. */
export function diagnosticFrames(error: unknown): string {
  try {
    if (!(error instanceof Error) || typeof error.stack !== 'string') return '';
    const frames = new Set<string>();
    for (const line of error.stack
      .slice(0, 64 * 1024)
      .split('\n')
      .slice(1, 65)) {
      const match = line.replace(/\\/g, '/').match(/([^\s()]+):(\d+):(\d+)\)?\s*$/);
      if (!match) continue;
      const [, raw, row, column] = match;
      if (+row < 1 || +column < 1 || +row > 10_000_000 || +column > 10_000_000) continue;
      let path = '';
      if (raw.includes('electron/src/')) path = raw.slice(raw.indexOf('electron/src/'));
      else {
        const url = new URL(raw, window.location.href);
        if (url.protocol !== window.location.protocol || url.host !== window.location.host)
          continue;
        if (url.pathname.startsWith('/assets/')) path = 'renderer';
        else if (url.pathname.startsWith('/src/')) path = `electron/src/renderer${url.pathname}`;
      }
      if (!path) continue;
      if (!framePaths.has(path)) path = 'renderer';
      frames.add(`${path}:${row}:${column}`);
      if ([...frames].join('\n').length > schema.limits.frame_bytes) {
        frames.delete(`${path}:${row}:${column}`);
        break;
      }
    }
    return [...frames].join('\n');
  } catch {
    return '';
  }
}

/** Typed technical fields only; failure reporting never affects the original operation. */
export function captureDiagnosticException(
  error: unknown,
  category: DiagnosticCategory,
  stage: DiagnosticStage,
): void {
  try {
    if (
      !diagnosticsAllowed() ||
      !schema.categories.includes(category) ||
      !schema.stages.includes(stage)
    )
      return;
    const code = errorCodeOf(error);
    // A classified compatibility failure may use HTTP 400; routine 4xx stays local.
    if (
      error &&
      typeof error === 'object' &&
      'status' in error &&
      typeof error.status === 'number' &&
      error.status >= 400 &&
      error.status < 500 &&
      (error.status === 401 || error.status === 403 || !code || expectedCodes.has(code))
    )
      return;
    if (error && typeof error === 'object') {
      if (seenErrors.has(error)) return;
      seenErrors.add(error);
    }
    const observedName = error instanceof Error ? error.name : 'OtherError';
    const name =
      typeof observedName === 'string' && observedName.length <= 64 && errorTypes.has(observedName)
        ? observedName
        : 'OtherError';
    const frames = diagnosticFrames(error);
    const input: DiagnosticInput = {
      category,
      source: 'renderer',
      stage,
      error_type: name,
      ...(code ? { error_code: code } : {}),
      ...(frames ? { frames } : {}),
    };
    const bridge = getBridge()?.diagnostics;
    if (bridge) {
      void bridge.capture(input).catch(() => {});
      return;
    }
    if (deliveries.size >= schema.limits.queue_count) return;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), schema.limits.request_timeout_ms);
    deliveries.set(controller, timer);
    void apiJson('/api/settings/diagnostics/event', {
      method: 'POST',
      body: JSON.stringify(input),
      signal: controller.signal,
    })
      .catch(() => {})
      .finally(() => {
        clearTimeout(timer);
        deliveries.delete(controller);
      });
  } catch {
    /* Never create a recursive renderer failure. */
  }
}
