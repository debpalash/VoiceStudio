import { isBenignWindowError } from '@shared/utils/foreignErrors';
import { scrubText } from '@shared/utils/scrub';
import { openRepairAgent } from '@/lib/repair-agent-events';
import {
  captureDiagnosticException,
  initializeDiagnosticReporting,
  observeDiagnosticConsent,
  type DiagnosticConsentObservation,
} from './diagnostic-reports';
import { captureException, type AnalyticsExceptionStage } from '@shared/utils/analytics';

const THROTTLE_MS = 30_000;
const lastOpened = new Map<string, number>();
let installed = false;
const taskObservations = new WeakMap<Promise<unknown>, DiagnosticConsentObservation | null>();

interface EarlyRendererFault {
  kind: 'error' | 'rejection';
  message: string;
  error: unknown;
  filename: string;
}

type EarlyErrorWindow = Window & {
  __voicestudioEarlyFaults?: EarlyRendererFault[];
  __voicestudioStopEarlyErrorCapture?: () => void;
};

function shouldRepair(message: string, error: unknown, filename = ''): boolean {
  if (isBenignWindowError(message, error, filename)) return false;
  const key = message.slice(0, 200);
  const now = Date.now();
  if ((lastOpened.get(key) || 0) > now - THROTTLE_MS) return false;
  lastOpened.set(key, now);
  return true;
}

function repair(
  message: string,
  error: unknown,
  filename = '',
  stage: AnalyticsExceptionStage = 'renderer:task',
  reportDiagnostics = true,
  observation: DiagnosticConsentObservation | null = null,
): void {
  if (isBenignWindowError(message, error, filename)) return;
  if (reportDiagnostics)
    captureDiagnosticException(
      error,
      stage === 'renderer:task' ? 'operation_failed' : 'renderer_crash',
      stage === 'renderer:task' ? 'other' : 'render',
      observation,
    );
  if (!shouldRepair(message, error, filename)) return;
  captureException(error, stage);
  const detail = error instanceof Error ? error.stack || error.message : String(error ?? message);
  openRepairAgent(
    scrubText(
      [
        'UNCAUGHT_RENDERER_ERROR',
        `Route: ${window.location.hash || '/'}`,
        message,
        detail || message,
      ].join('\n'),
    ),
    true,
  );
}

/**
 * Start a new semantic action under its observed grant and bind its returned promise.
 * The factory must start the work, not return unknown work already in flight.
 * Known promises keep their first owner (including OFF); derived promises need
 * their own producer-bound wrapper, never authority inferred from delivery time.
 */
export function rendererTask<T>(task: () => T | PromiseLike<T>): Promise<T> {
  const observation = observeDiagnosticConsent();
  let promise: Promise<T>;
  try {
    promise = Promise.resolve(task());
  } catch (error) {
    promise = Promise.reject(error);
  }
  // Weak keys retain no completed work, Error objects, or guessed temporal context.
  if (!taskObservations.has(promise)) taskObservations.set(promise, observation);
  return promise;
}

/** Run an event-triggered task without letting sync throws or promise rejections escape React. */
export function runRendererTask(label: string, task: () => void | Promise<void>): void {
  const observation = observeDiagnosticConsent();
  try {
    const result = task();
    if (result)
      void Promise.resolve(result).catch((error) =>
        repair(label, error, '', 'renderer:task', true, observation),
      );
  } catch (error) {
    repair(label, error, '', 'renderer:task', true, observation);
  }
}

/** Send uncaught asynchronous renderer faults to the opted-in local repair agent. */
export function installGlobalErrorRecovery(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  const target = window as EarlyErrorWindow;
  const earlyFaults = target.__voicestudioEarlyFaults?.splice(0) ?? [];
  // The bootstrap queue predates any observed grant. A later ON response may
  // reflect another client's new choice, so it cannot authorize this history.
  // Initialize for subsequent live faults without retaining early Error objects.
  void initializeDiagnosticReporting();
  target.__voicestudioStopEarlyErrorCapture?.();
  delete target.__voicestudioEarlyFaults;
  window.addEventListener('error', (event) => {
    const message = event.error?.message || event.message;
    if (isBenignWindowError(message, event.error, event.filename)) return;
    // The fault is now captured in the frontend log and repair-agent report.
    // Mark it handled so Chromium does not add a second noisy "Uncaught" entry.
    event.preventDefault();
    // ErrorEvent has no producer identity. A delayed timer/callback fault
    // cannot acquire current permission merely because it reached this hook.
    repair(message, event.error, event.filename, 'renderer:uncaught', true, null);
  });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    const message = reason?.message || String(reason);
    if (isBenignWindowError(message, reason)) return;
    event.preventDefault();
    // Rejection delivery can follow arbitrary asynchronous cleanup. Only this
    // exact producer promise can authorize it; an unknown origin stays local.
    repair(
      message,
      reason,
      '',
      'renderer:rejection',
      true,
      taskObservations.get(event.promise) ?? null,
    );
  });
  for (const fault of earlyFaults)
    repair(
      fault.message,
      fault.error,
      fault.filename,
      fault.kind === 'rejection' ? 'renderer:rejection' : 'renderer:uncaught',
      false,
    );
}
