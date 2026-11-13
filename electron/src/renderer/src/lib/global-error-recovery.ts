import { isBenignWindowError } from '@shared/utils/foreignErrors';
import { scrubText } from '@shared/utils/scrub';
import { openRepairAgent } from '@/lib/repair-agent-events';
import {
  captureException,
  type AnalyticsExceptionStage,
} from '@shared/utils/analytics';

const THROTTLE_MS = 30_000;
const lastOpened = new Map<string, number>();
let installed = false;

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
): void {
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

/** Run an event-triggered task without letting sync throws or promise rejections escape React. */
export function runRendererTask(label: string, task: () => void | Promise<void>): void {
  try {
    const result = task();
    if (result)
      void Promise.resolve(result).catch((error) => repair(label, error, '', 'renderer:task'));
  } catch (error) {
    repair(label, error, '', 'renderer:task');
  }
}

/** Send uncaught asynchronous renderer faults to the opted-in local repair agent. */
export function installGlobalErrorRecovery(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  const target = window as EarlyErrorWindow;
  const earlyFaults = target.__voicestudioEarlyFaults?.splice(0) ?? [];
  target.__voicestudioStopEarlyErrorCapture?.();
  delete target.__voicestudioEarlyFaults;
  window.addEventListener('error', (event) => {
    const message = event.error?.message || event.message;
    if (isBenignWindowError(message, event.error, event.filename)) return;
    // The fault is now captured in the frontend log and repair-agent report.
    // Mark it handled so Chromium does not add a second noisy "Uncaught" entry.
    event.preventDefault();
    repair(message, event.error, event.filename, 'renderer:uncaught');
  });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    const message = reason?.message || String(reason);
    if (isBenignWindowError(message, reason)) return;
    event.preventDefault();
    repair(message, reason, '', 'renderer:rejection');
  });
  for (const fault of earlyFaults)
    repair(
      fault.message,
      fault.error,
      fault.filename,
      fault.kind === 'rejection' ? 'renderer:rejection' : 'renderer:uncaught',
    );
}
