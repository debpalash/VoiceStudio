import type { PersistStorage, StorageValue } from 'zustand/middleware';

export type PersistenceRole = 'unknown' | 'main' | 'readonly';
export type WritablePersistenceRole = Exclude<PersistenceRole, 'unknown'>;
export type StorageKeyPredicate = (key: string) => boolean;

export interface FlushSummary {
  attempted: number;
  written: number;
  skipped: number;
  failed: number;
  discarded: number;
}

type RawStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type TimerHandle = ReturnType<typeof setTimeout>;
type JsonProvider = () => unknown;

type StagedOperation =
  | { kind: 'set'; generation: number; provider: JsonProvider }
  | { kind: 'remove'; generation: number };

interface PendingWrite {
  generation: number;
  provider: JsonProvider;
  windowGeneration: number;
  quietTimer: TimerHandle | null;
  maximumTimer: TimerHandle | null;
}

interface ListenerTarget {
  addEventListener(type: string, listener: EventListener): void;
  removeEventListener(type: string, listener: EventListener): void;
}

interface VisibilityTarget extends ListenerTarget {
  readonly visibilityState: DocumentVisibilityState;
}

export interface CoalescedJsonStorageOptions {
  getStorage?: () => RawStorage;
  stringify?: (value: unknown) => string | undefined;
  parse?: (value: string) => unknown;
  setTimer?: (callback: () => void, delayMs: number) => TimerHandle;
  clearTimer?: (handle: TimerHandle) => void;
  now?: () => number;
  quietDelayMs?: number;
  maximumDelayMs?: number;
  warn?: (message: string) => void;
  getPagehideTarget?: () => ListenerTarget | null;
  getVisibilityTarget?: () => VisibilityTarget | null;
}

export interface CoalescedJsonStorageController {
  queueJsonWrite<T>(key: string, readLatestValue: () => T): () => void;
  createZustandJsonStorage<S>(): PersistStorage<S>;
  /** Read only the physical JSON value, propagating access/parse failures. */
  readDurableJsonValue<T>(key: string): T | null;
  flushPendingWrites(): FlushSummary;
  discardPendingWrites(predicate?: StorageKeyPredicate): number;
  suspendJsonWrites(predicate: StorageKeyPredicate): () => void;
  configurePersistenceRole(role: WritablePersistenceRole): void;
  getPersistenceRole(): PersistenceRole;
  installPersistenceLifecycleFlush(): () => void;
  resetForTests(): void;
}

const DEFAULT_QUIET_DELAY_MS = 250;
const DEFAULT_MAXIMUM_DELAY_MS = 1_000;

function emptySummary(): FlushSummary {
  return { attempted: 0, written: 0, skipped: 0, failed: 0, discarded: 0 };
}

function safeErrorClass(error: unknown): string {
  let candidate = '';
  try {
    if (error !== null && typeof error === 'object') {
      const name = (error as { name?: unknown }).name;
      if (typeof name === 'string') candidate = name;
    }
  } catch {
    return 'UnknownError';
  }
  return /^[A-Za-z][A-Za-z0-9]*Error$/.test(candidate) ? candidate : 'UnknownError';
}

/**
 * Build an isolated persistence scheduler. Production uses the singleton
 * exports below; tests can inject storage, clocks, and lifecycle targets.
 */
export function createCoalescedJsonStorage(
  options: CoalescedJsonStorageOptions = {},
): CoalescedJsonStorageController {
  const getStorage =
    options.getStorage ??
    (() => {
      if (typeof localStorage === 'undefined') throw new Error('StorageUnavailableError');
      return localStorage;
    });
  const stringify = options.stringify ?? JSON.stringify;
  const parse = options.parse ?? JSON.parse;
  const setTimer =
    options.setTimer ??
    ((callback: () => void, delayMs: number) => globalThis.setTimeout(callback, delayMs));
  const clearTimer =
    options.clearTimer ?? ((handle: TimerHandle) => globalThis.clearTimeout(handle));
  const now = options.now ?? (() => Date.now());
  const quietDelayMs = options.quietDelayMs ?? DEFAULT_QUIET_DELAY_MS;
  const maximumDelayMs = options.maximumDelayMs ?? DEFAULT_MAXIMUM_DELAY_MS;
  const warn = options.warn ?? ((message: string) => console.warn(message));
  const getPagehideTarget =
    options.getPagehideTarget ??
    (() => (typeof window === 'undefined' ? null : (window as ListenerTarget)));
  const getVisibilityTarget =
    options.getVisibilityTarget ??
    (() => (typeof document === 'undefined' ? null : (document as VisibilityTarget)));

  if (quietDelayMs < 0 || maximumDelayMs < quietDelayMs) {
    throw new RangeError('Persistence delays must satisfy 0 <= quiet <= maximum');
  }

  let role: PersistenceRole = 'unknown';
  let nextGeneration = 0;
  let nextWindowGeneration = 0;
  let nextSuspensionId = 0;
  const pending = new Map<string, PendingWrite>();
  const staged = new Map<string, StagedOperation>();
  const suspensions = new Map<number, StorageKeyPredicate>();
  const warnedFailures = new Set<string>();
  let lifecycleCleanup: (() => void) | null = null;

  function isSuspended(key: string): boolean {
    for (const predicate of suspensions.values()) {
      if (predicate(key)) return true;
    }
    return false;
  }

  function warnFailure(key: string, operation: string, error: unknown): void {
    const errorClass = safeErrorClass(error);
    const warningId = `${key}\u0000${operation}\u0000${errorClass}`;
    if (warnedFailures.has(warningId)) return;
    warnedFailures.add(warningId);
    // Deliberately omit the error object/message and every persisted value.
    warn(`[persistence] ${operation} failed for ${key} (${errorClass})`);
  }

  function clearEntryTimers(entry: PendingWrite): void {
    if (entry.quietTimer !== null) clearTimer(entry.quietTimer);
    if (entry.maximumTimer !== null) clearTimer(entry.maximumTimer);
    entry.quietTimer = null;
    entry.maximumTimer = null;
  }

  function cancelPending(key: string): boolean {
    const entry = pending.get(key);
    if (!entry) return false;
    clearEntryTimers(entry);
    pending.delete(key);
    return true;
  }

  function discardInvalidEntry(key: string, entry: PendingWrite): void {
    if (pending.get(key) !== entry) return;
    clearEntryTimers(entry);
    pending.delete(key);
  }

  function flushKey(
    key: string,
    expected?: { generation?: number; windowGeneration?: number },
  ): FlushSummary {
    const summary = emptySummary();
    if (role !== 'main') return summary;

    const entry = pending.get(key);
    if (!entry) return summary;
    if (expected?.generation !== undefined && entry.generation !== expected.generation) {
      return summary;
    }
    if (
      expected?.windowGeneration !== undefined &&
      entry.windowGeneration !== expected.windowGeneration
    ) {
      return summary;
    }

    summary.attempted = 1;
    clearEntryTimers(entry);

    let value: unknown;
    try {
      value = entry.provider();
    } catch (error) {
      warnFailure(key, 'provide', error);
      discardInvalidEntry(key, entry);
      summary.failed = 1;
      summary.discarded = 1;
      return summary;
    }
    if (pending.get(key) !== entry) return summary;

    let rawValue: string | undefined;
    try {
      rawValue = stringify(value);
      if (rawValue === undefined) throw new TypeError('JsonSerializationError');
    } catch (error) {
      warnFailure(key, 'serialize', error);
      discardInvalidEntry(key, entry);
      summary.failed = 1;
      summary.discarded = 1;
      return summary;
    }
    if (pending.get(key) !== entry) return summary;

    let storage: RawStorage;
    try {
      storage = getStorage();
    } catch (error) {
      warnFailure(key, 'access', error);
      summary.failed = 1;
      return summary;
    }

    let durableValue: string | null = null;
    let durableReadSucceeded = false;
    try {
      durableValue = storage.getItem(key);
      durableReadSucceeded = true;
    } catch (error) {
      warnFailure(key, 'read', error);
    }

    if (durableReadSucceeded && durableValue === rawValue) {
      if (pending.get(key) === entry) pending.delete(key);
      summary.skipped = 1;
      return summary;
    }

    if (pending.get(key) !== entry) return summary;
    try {
      storage.setItem(key, rawValue);
    } catch (error) {
      warnFailure(key, 'write', error);
      // Keep the latest provider dirty, but leave it timer-disarmed. A later
      // queue or explicit/lifecycle flush is the only retry trigger.
      summary.failed = 1;
      return summary;
    }

    if (pending.get(key) === entry) pending.delete(key);
    summary.written = 1;
    return summary;
  }

  function scheduleFreshWindow(entry: PendingWrite, key: string): void {
    const windowGeneration = entry.windowGeneration;
    const firstQueuedAt = now();
    entry.quietTimer = setTimer(
      () => flushKey(key, { generation: entry.generation }),
      quietDelayMs,
    );
    entry.maximumTimer = setTimer(
      () => {
        flushKey(key, { windowGeneration });
      },
      Math.max(0, firstQueuedAt + maximumDelayMs - now()),
    );
  }

  function activateSet(key: string, generation: number, provider: JsonProvider): void {
    if (isSuspended(key) || role !== 'main') return;

    const previous = pending.get(key);
    const hasActiveWindow =
      previous !== undefined && (previous.quietTimer !== null || previous.maximumTimer !== null);

    if (hasActiveWindow) {
      if (previous.quietTimer !== null) clearTimer(previous.quietTimer);
      const replacement: PendingWrite = {
        generation,
        provider,
        windowGeneration: previous.windowGeneration,
        quietTimer: setTimer(() => flushKey(key, { generation }), quietDelayMs),
        maximumTimer: previous.maximumTimer,
      };
      // The maximum callback resolves the current entry by window generation,
      // so it flushes this replacement while rejecting an obsolete window.
      pending.set(key, replacement);
      return;
    }

    if (previous) clearEntryTimers(previous);
    const entry: PendingWrite = {
      generation,
      provider,
      windowGeneration: ++nextWindowGeneration,
      quietTimer: null,
      maximumTimer: null,
    };
    pending.set(key, entry);
    scheduleFreshWindow(entry, key);
  }

  function queueJsonWrite<T>(key: string, readLatestValue: () => T): () => void {
    if (typeof key !== 'string' || key.length === 0) {
      throw new TypeError('Persistence key must be a non-empty string');
    }
    if (typeof readLatestValue !== 'function') {
      throw new TypeError('Persistence provider must be a function');
    }
    if (role === 'readonly' || isSuspended(key)) return () => {};

    const generation = ++nextGeneration;
    if (role === 'unknown') {
      staged.set(key, { kind: 'set', generation, provider: readLatestValue });
    } else {
      activateSet(key, generation, readLatestValue);
    }

    let disposed = false;
    return () => {
      if (disposed) return;
      disposed = true;
      const stagedOperation = staged.get(key);
      if (stagedOperation?.generation === generation) staged.delete(key);
      const pendingWrite = pending.get(key);
      if (pendingWrite?.generation === generation) cancelPending(key);
    };
  }

  function readPendingValue(key: string): { found: boolean; value: unknown } {
    const stagedOperation = staged.get(key);
    if (stagedOperation?.kind === 'remove') return { found: true, value: null };
    const provider =
      stagedOperation?.kind === 'set' ? stagedOperation.provider : pending.get(key)?.provider;
    if (!provider) return { found: false, value: null };

    try {
      return { found: true, value: provider() };
    } catch (error) {
      warnFailure(key, 'provide', error);
      if (stagedOperation?.kind === 'set') staged.delete(key);
      else cancelPending(key);
      return { found: false, value: null };
    }
  }

  function readDurableValue(key: string): unknown | null {
    let rawValue: string | null;
    try {
      rawValue = getStorage().getItem(key);
    } catch (error) {
      warnFailure(key, 'read', error);
      return null;
    }
    if (rawValue === null) return null;
    try {
      return parse(rawValue);
    } catch (error) {
      warnFailure(key, 'parse', error);
      return null;
    }
  }

  function readDurableJsonValue<T>(key: string): T | null {
    const rawValue = getStorage().getItem(key);
    return rawValue === null ? null : (parse(rawValue) as T);
  }

  function removeItem(key: string): void {
    cancelPending(key);
    staged.delete(key);
    const generation = ++nextGeneration;

    if (role === 'unknown') {
      staged.set(key, { kind: 'remove', generation });
      return;
    }
    if (role === 'readonly') return;

    // Removal is deliberately truthful in the main window: callers such as
    // persist.clearStorage() must observe storage access/removal failures.
    getStorage().removeItem(key);
  }

  function createZustandJsonStorage<S>(): PersistStorage<S> {
    return {
      getItem(name: string): StorageValue<S> | null {
        const latest = readPendingValue(name);
        if (latest.found) return latest.value as StorageValue<S> | null;
        return readDurableValue(name) as StorageValue<S> | null;
      },
      setItem(name: string, value: StorageValue<S>): void {
        queueJsonWrite(name, () => value);
      },
      removeItem,
    };
  }

  function flushPendingWrites(): FlushSummary {
    const total = emptySummary();
    if (role !== 'main') return total;
    for (const key of Array.from(pending.keys())) {
      const result = flushKey(key);
      total.attempted += result.attempted;
      total.written += result.written;
      total.skipped += result.skipped;
      total.failed += result.failed;
      total.discarded += result.discarded;
    }
    return total;
  }

  function discardPendingWrites(predicate: StorageKeyPredicate = () => true): number {
    const keys = new Set([...pending.keys(), ...staged.keys()]);
    let discarded = 0;
    for (const key of keys) {
      if (!predicate(key)) continue;
      const hadPending = cancelPending(key);
      const hadStaged = staged.delete(key);
      if (hadPending || hadStaged) discarded += 1;
    }
    return discarded;
  }

  function suspendJsonWrites(predicate: StorageKeyPredicate): () => void {
    discardPendingWrites(predicate);
    const suspensionId = ++nextSuspensionId;
    suspensions.set(suspensionId, predicate);
    let resumed = false;
    return () => {
      if (resumed) return;
      resumed = true;
      suspensions.delete(suspensionId);
    };
  }

  function configurePersistenceRole(nextRole: WritablePersistenceRole): void {
    if (role === nextRole) return;
    if (role !== 'unknown') {
      throw new Error(`Persistence role is already ${role}`);
    }
    role = nextRole;

    const operations = [...staged.entries()];
    staged.clear();
    if (nextRole === 'readonly') {
      for (const entry of pending.values()) clearEntryTimers(entry);
      pending.clear();
      return;
    }

    let firstRemovalError: unknown;
    for (const [key, operation] of operations) {
      if (operation.kind === 'set') {
        if (isSuspended(key)) continue;
        activateSet(key, operation.generation, operation.provider);
        continue;
      }
      try {
        getStorage().removeItem(key);
      } catch (error) {
        firstRemovalError ??= error;
      }
    }
    if (firstRemovalError !== undefined) throw firstRemovalError;
  }

  function getPersistenceRole(): PersistenceRole {
    return role;
  }

  function installPersistenceLifecycleFlush(): () => void {
    if (role !== 'main') return () => {};
    if (lifecycleCleanup) return lifecycleCleanup;

    const pagehideTarget = getPagehideTarget();
    const visibilityTarget = getVisibilityTarget();
    const onPagehide: EventListener = () => {
      flushPendingWrites();
    };
    const onVisibilityChange: EventListener = () => {
      if (visibilityTarget?.visibilityState === 'hidden') flushPendingWrites();
    };
    pagehideTarget?.addEventListener('pagehide', onPagehide);
    visibilityTarget?.addEventListener('visibilitychange', onVisibilityChange);

    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      pagehideTarget?.removeEventListener('pagehide', onPagehide);
      visibilityTarget?.removeEventListener('visibilitychange', onVisibilityChange);
      if (lifecycleCleanup === cleanup) lifecycleCleanup = null;
    };
    lifecycleCleanup = cleanup;
    return cleanup;
  }

  function resetForTests(): void {
    for (const entry of pending.values()) clearEntryTimers(entry);
    pending.clear();
    staged.clear();
    suspensions.clear();
    warnedFailures.clear();
    lifecycleCleanup?.();
    lifecycleCleanup = null;
    role = 'unknown';
    nextGeneration = 0;
    nextWindowGeneration = 0;
    nextSuspensionId = 0;
  }

  return {
    queueJsonWrite,
    createZustandJsonStorage,
    readDurableJsonValue,
    flushPendingWrites,
    discardPendingWrites,
    suspendJsonWrites,
    configurePersistenceRole,
    getPersistenceRole,
    installPersistenceLifecycleFlush,
    resetForTests,
  };
}

const applicationStorage = createCoalescedJsonStorage();

export const queueJsonWrite = applicationStorage.queueJsonWrite;
export const createZustandJsonStorage = applicationStorage.createZustandJsonStorage;
export const readDurableJsonValue = applicationStorage.readDurableJsonValue;
export const flushPendingWrites = applicationStorage.flushPendingWrites;
export const discardPendingWrites = applicationStorage.discardPendingWrites;
export const suspendJsonWrites = applicationStorage.suspendJsonWrites;
export const configurePersistenceRole = applicationStorage.configurePersistenceRole;
export const getPersistenceRole = applicationStorage.getPersistenceRole;
export const installPersistenceLifecycleFlush = applicationStorage.installPersistenceLifecycleFlush;

/** Test/HMR teardown for the application singleton. Never clears durable data. */
export const resetCoalescedJsonStorageForTests = applicationStorage.resetForTests;
