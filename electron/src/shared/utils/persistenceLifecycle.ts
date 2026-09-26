import {
  flushPendingWrites as flushLocalPendingWrites,
  type FlushSummary,
} from './coalescedJsonStorage';
import { flushLongformPendingWritesForExit } from './longformPersistence';

export const DESKTOP_PERSISTENCE_FLUSH_EVENT = 'persistence://flush-requested';
export const CONFIRM_PERSISTENCE_FLUSH_COMMAND = 'confirm_persistence_flush';
export const PERSISTENCE_FLUSH_TIMEOUT_ERROR = 'PersistenceFlushTimeoutError';
const DEFAULT_PERSISTENCE_FLUSH_TIMEOUT_MS = 2_000;

type Unlisten = () => void;
type Listen = (event: string, handler: () => void) => Promise<Unlisten>;

export interface PersistenceFlushDependencies {
  flushLongform?: () => Promise<void>;
  flushLocal?: () => FlushSummary;
  warn?: (message: string, error?: unknown) => void;
  timeoutMs?: number;
}

export interface DesktopExitHandshakeDependencies extends PersistenceFlushDependencies {
  desktop?: boolean;
  listen?: Listen;
  confirm?: () => Promise<unknown>;
}

function defaultWarn(message: string, error?: unknown): void {
  console.warn(message, error);
}

async function settleLongformWithinDeadline(
  flush: () => Promise<void>,
  timeoutMs: number,
): Promise<void> {
  const task = flush();
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_resolve, reject) => {
    timeout = globalThis.setTimeout(() => {
      const error = new Error('Long-form persistence did not settle before the exit deadline');
      error.name = PERSISTENCE_FLUSH_TIMEOUT_ERROR;
      reject(error);
    }, timeoutMs);
  });
  try {
    await Promise.race([task, deadline]);
  } finally {
    if (timeout !== undefined) globalThis.clearTimeout(timeout);
  }
}

/**
 * Settle the async document store first, then synchronously drain the compact
 * local envelope. The order matters: an IndexedDB failure materializes a full
 * local fallback, while a successful commit queues the compact envelope.
 */
export async function flushApplicationPersistence(
  dependencies: PersistenceFlushDependencies = {},
): Promise<FlushSummary> {
  const flushLongform = dependencies.flushLongform ?? flushLongformPendingWritesForExit;
  const flushLocal = dependencies.flushLocal ?? flushLocalPendingWrites;
  const warn = dependencies.warn ?? defaultWarn;
  const timeoutMs = dependencies.timeoutMs ?? DEFAULT_PERSISTENCE_FLUSH_TIMEOUT_MS;

  let longformFailed = false;
  let longformError: unknown;
  try {
    await settleLongformWithinDeadline(flushLongform, timeoutMs);
  } catch (error) {
    longformFailed = true;
    longformError = error;
  }
  const summary = flushLocal();
  if (summary.failed > 0) {
    warn(`[persistence] ${summary.failed} local write(s) could not be flushed`);
  }
  if (longformFailed) throw longformError;
  return summary;
}

/** Run an intentional reload/relaunch only after both persistence layers settle. */
export async function afterApplicationPersistence<T>(
  action: () => T | Promise<T>,
  dependencies: PersistenceFlushDependencies = {},
): Promise<T> {
  const warn = dependencies.warn ?? defaultWarn;
  try {
    await flushApplicationPersistence(dependencies);
  } catch (error) {
    // An explicit reload/relaunch must remain available as a recovery action
    // when persistence itself is blocked. Adapters have already retained every
    // durable copy they could; match the bounded native-exit handshake.
    warn('[persistence] pre-action flush failed', error);
  }
  return await action();
}

export function reloadAfterApplicationPersistence(
  reload: () => void = () => window.location.reload(),
): Promise<void> {
  return afterApplicationPersistence(reload);
}

/**
 * Complete the native half of an orderly desktop exit. Rust prevents the
 * first ExitRequested event and emits this request; only after the document
 * and compact stores settle do we confirm that the process may terminate.
 * Rust owns the deadline, so a hung/destroyed webview still exits safely.
 */
export async function installDesktopPersistenceExitHandshake(
  dependencies: DesktopExitHandshakeDependencies = {},
): Promise<Unlisten> {
  const desktop = dependencies.desktop ?? (typeof window !== 'undefined' && !!window.voicestudio);
  if (!desktop) return () => {};

  const warn = dependencies.warn ?? defaultWarn;
  if (!dependencies.listen && !dependencies.confirm && window.voicestudio) {
    return window.voicestudio.app.onPersistenceFlush(async () => {
      try {
        await flushApplicationPersistence(dependencies);
      } catch (error) {
        warn('[persistence] orderly exit flush failed', error);
      }
    });
  }
  let listen = dependencies.listen;
  if (!listen) return () => {};
  const confirm = dependencies.confirm ?? (async () => {});
  let completion: Promise<void> | null = null;

  try {
    return await listen(DESKTOP_PERSISTENCE_FLUSH_EVENT, () => {
      // Repeated OS quit requests while the first one is waiting must not start
      // concurrent IndexedDB transactions or acknowledge the same exit twice.
      completion ??= (async () => {
        try {
          await flushApplicationPersistence(dependencies);
        } catch (error) {
          // Persistence failures are already surfaced by their adapters. Do not
          // strand an app that the user explicitly asked to quit; native timeout
          // is the final backstop if this acknowledgement also fails.
          warn('[persistence] orderly exit flush failed', error);
        }
        try {
          await confirm();
        } catch (error) {
          warn('[persistence] orderly exit confirmation failed', error);
        }
      })();
    });
  } catch (error) {
    warn('[persistence] native exit listener could not register', error);
    return () => {};
  }
}
