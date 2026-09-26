import type { PersistStorage, StorageValue } from 'zustand/middleware';

import {
  createZustandJsonStorage,
  flushPendingWrites as flushLocalPendingWrites,
  getPersistenceRole,
  readDurableJsonValue,
  type FlushSummary,
  type PersistenceRole,
} from './coalescedJsonStorage';
import {
  createIndexedDbLongformStore,
  LONGFORM_DB_NAME,
  LONGFORM_DB_SCHEMA,
  type DurableLongformRecord,
  type LongformDurableStore,
} from './indexedDbLongformStore';

export type { DurableLongformRecord, LongformDurableStore } from './indexedDbLongformStore';

const QUIET_DELAY_MS = 250;
const MAXIMUM_DELAY_MS = 1_000;
const DEFAULT_READ_ATTEMPTS = 3;
const FALLBACK_REVISION_FIELD = 'longformFallbackRevision';
const PENDING_DURABLE_CLEAR_FIELD = 'longformPendingDurableClear';
const PENDING_STORAGE_REMOVE_FIELD = 'longformPendingStorageRemove';
const HYDRATION_ERROR_FIELD = 'longformPersistenceError';
export const LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR = 'LongformLocalFallbackClearError';
export const LONGFORM_LOCAL_FALLBACK_WRITE_ERROR = 'LongformLocalFallbackWriteError';

/** Fields whose size grows with a manuscript, cast, or saved-project count. */
const UNBOUNDED_LONGFORM_KEYS = [
  'storyTracks',
  'cast',
  'storyProjects',
  'script',
  'meta',
  'lexicon',
  'voiceCast',
  'lastOutputScript',
  'lastOutputChapters',
] as const;

type JsonObject = Record<string, unknown>;
type TimerHandle = ReturnType<typeof setTimeout>;

interface ListenerTarget {
  addEventListener(type: string, listener: EventListener): void;
  removeEventListener(type: string, listener: EventListener): void;
}

interface VisibilityTarget extends ListenerTarget {
  readonly visibilityState: DocumentVisibilityState;
}

export interface LongformPersistenceOptions<S> {
  localStorage: PersistStorage<S>;
  durableStore: LongformDurableStore | (() => LongformDurableStore);
  currentVersion: number;
  getRole?: () => PersistenceRole;
  setTimer?: (callback: () => void, delayMs: number) => TimerHandle;
  clearTimer?: (handle: TimerHandle) => void;
  now?: () => number;
  quietDelayMs?: number;
  maximumDelayMs?: number;
  readAttempts?: number;
  warn?: (message: string) => void;
  flushLocalStorage?: () => FlushSummary;
  readDurableLocalStorage?: (
    name: string,
  ) => StorageValue<S> | null | Promise<StorageValue<S> | null>;
  getPagehideTarget?: () => ListenerTarget | null;
  getVisibilityTarget?: () => VisibilityTarget | null;
}

export interface LongformPersistenceController<S> {
  storage: PersistStorage<S>;
  flushPendingWrites(): Promise<void>;
  flushPendingWritesForExit(): Promise<void>;
  discardPendingWrites(): boolean;
  clearDurable(): Promise<void>;
  installLifecycleFlush(): () => void;
  reset(): void;
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

function asObject(value: unknown): JsonObject {
  return value !== null && typeof value === 'object' ? (value as JsonObject) : {};
}

function pickPayloadReferences(state: JsonObject): JsonObject {
  return Object.fromEntries(
    UNBOUNDED_LONGFORM_KEYS.filter((key) => Object.prototype.hasOwnProperty.call(state, key)).map(
      (key) => [key, state[key]],
    ),
  );
}

function samePayloadReferences(left: JsonObject | null, right: JsonObject): boolean {
  return left !== null && UNBOUNDED_LONGFORM_KEYS.every((key) => Object.is(left[key], right[key]));
}

function sanitizeTrack(track: unknown): unknown {
  if (track === null || typeof track !== 'object') return track;
  const value = track as JsonObject;
  return {
    id: value.id,
    character: value.character,
    text: value.text,
    profileId: value.profileId,
    emotion: value.emotion,
    speed: value.speed,
  };
}

function extractLongformPayload(stateValue: unknown): JsonObject {
  const state = asObject(stateValue);
  const payload = pickPayloadReferences(state);
  if (Array.isArray(payload.storyTracks)) {
    payload.storyTracks = payload.storyTracks.map(sanitizeTrack);
  }
  return payload;
}

function compactLongformState(stateValue: unknown): JsonObject {
  const compact = { ...asObject(stateValue) };
  for (const key of UNBOUNDED_LONGFORM_KEYS) delete compact[key];
  return compact;
}

function resetLongformState(stateValue: unknown): JsonObject {
  return {
    ...compactLongformState(stateValue),
    currentProjectId: null,
    coverRef: null,
    lastOutput: '',
  };
}

function containsLongformPayload(stateValue: unknown): boolean {
  const state = asObject(stateValue);
  return UNBOUNDED_LONGFORM_KEYS.some((key) => Object.prototype.hasOwnProperty.call(state, key));
}

/** Retain only project data/tombstones while resetting bounded preferences. */
export function preserveRevisionedLongformFallback(rawValue: string | null): string | null {
  if (rawValue === null) return null;
  try {
    const parsed = JSON.parse(rawValue) as unknown;
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    const envelope = parsed as JsonObject;
    const revision = envelope[FALLBACK_REVISION_FIELD];
    const pendingClear = envelope[PENDING_DURABLE_CLEAR_FIELD] === true;
    const pendingRemove = pendingClear && envelope[PENDING_STORAGE_REMOVE_FIELD] === true;
    if (!pendingClear && !containsLongformPayload(envelope.state)) {
      return null;
    }
    const preserved: JsonObject = {
      state: extractLongformPayload(envelope.state),
    };
    if (validRevision(revision) && revision > 0) {
      preserved[FALLBACK_REVISION_FIELD] = revision;
    }
    if (pendingClear) preserved[PENDING_DURABLE_CLEAR_FIELD] = true;
    if (pendingRemove) preserved[PENDING_STORAGE_REMOVE_FIELD] = true;
    if (Object.prototype.hasOwnProperty.call(envelope, 'version')) {
      preserved.version = envelope.version;
    }
    return JSON.stringify(preserved);
  } catch {
    return null;
  }
}

function mergePayload(stateValue: unknown, payload: JsonObject): JsonObject {
  return { ...compactLongformState(stateValue), ...payload };
}

type VersionedStorageValue<S> = StorageValue<S> & {
  [FALLBACK_REVISION_FIELD]?: number;
  [PENDING_DURABLE_CLEAR_FIELD]?: boolean;
  [PENDING_STORAGE_REMOVE_FIELD]?: boolean;
};

function validRevision(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function fallbackRevision<S>(value: StorageValue<S> | null): number {
  if (!value) return 0;
  const revision = (value as VersionedStorageValue<S>)[FALLBACK_REVISION_FIELD];
  return validRevision(revision) ? revision : 0;
}

function durableRevision(value: DurableLongformRecord | null): number {
  return validRevision(value?.revision) ? value.revision : 0;
}

function compactEnvelope<S>(value: StorageValue<S>): StorageValue<S> {
  const compact = { ...value } as VersionedStorageValue<S>;
  delete compact[FALLBACK_REVISION_FIELD];
  delete compact[PENDING_DURABLE_CLEAR_FIELD];
  delete compact[PENDING_STORAGE_REMOVE_FIELD];
  compact.state = compactLongformState(value.state) as S;
  return compact;
}

function resetEnvelope<S>(value: StorageValue<S>): StorageValue<S> {
  const reset = compactEnvelope(value);
  reset.state = resetLongformState(value.state) as S;
  return reset;
}

function pendingClearEnvelope<S>(value: StorageValue<S>): VersionedStorageValue<S> {
  return {
    ...resetEnvelope(value),
    [PENDING_DURABLE_CLEAR_FIELD]: true,
  };
}

function pendingRemoveEnvelope<S>(currentVersion: number): VersionedStorageValue<S> {
  return {
    version: currentVersion,
    state: {} as S,
    [PENDING_DURABLE_CLEAR_FIELD]: true,
    [PENDING_STORAGE_REMOVE_FIELD]: true,
  };
}

function withoutPendingClear<S>(value: StorageValue<S>): StorageValue<S> {
  const next = { ...value } as VersionedStorageValue<S>;
  delete next[PENDING_DURABLE_CLEAR_FIELD];
  delete next[PENDING_STORAGE_REMOVE_FIELD];
  return next;
}

function fullEnvelope<S>(
  value: StorageValue<S>,
  revision: number,
  pendingDurableClear = false,
): VersionedStorageValue<S> {
  const full = {
    ...value,
    [FALLBACK_REVISION_FIELD]: revision,
    state: {
      ...compactLongformState(value.state),
      ...extractLongformPayload(value.state),
    } as S,
  } as VersionedStorageValue<S>;
  if (pendingDurableClear) {
    (full as JsonObject)[PENDING_DURABLE_CLEAR_FIELD] = true;
  }
  return full;
}

interface PendingWrite<S> {
  name: string;
  value: StorageValue<S>;
  payloadReferences: JsonObject;
  generation: number;
  revision: number;
  windowGeneration: number;
  quietTimer: TimerHandle | null;
  maximumTimer: TimerHandle | null;
}

/**
 * Split Zustand persistence across bounded localStorage and durable IndexedDB.
 * IndexedDB is always committed before a legacy full envelope is compacted.
 */
export function createLongformPersistence<S>(
  options: LongformPersistenceOptions<S>,
): LongformPersistenceController<S> {
  const resolveDurableStore =
    typeof options.durableStore === 'function'
      ? (options.durableStore as () => LongformDurableStore)
      : () => options.durableStore as LongformDurableStore;
  const getRole = options.getRole ?? (() => 'main');
  const setTimer =
    options.setTimer ??
    ((callback: () => void, delayMs: number) => globalThis.setTimeout(callback, delayMs));
  const clearTimer =
    options.clearTimer ?? ((handle: TimerHandle) => globalThis.clearTimeout(handle));
  const now = options.now ?? (() => Date.now());
  const quietDelayMs = options.quietDelayMs ?? QUIET_DELAY_MS;
  const maximumDelayMs = options.maximumDelayMs ?? MAXIMUM_DELAY_MS;
  const readAttempts = options.readAttempts ?? DEFAULT_READ_ATTEMPTS;
  const warn = options.warn ?? ((message: string) => console.warn(message));
  const readDurableLocalStorage =
    options.readDurableLocalStorage ?? ((name: string) => options.localStorage.getItem(name));
  const getPagehideTarget =
    options.getPagehideTarget ??
    (() => (typeof window === 'undefined' ? null : (window as ListenerTarget)));
  const getVisibilityTarget =
    options.getVisibilityTarget ??
    (() => (typeof document === 'undefined' ? null : (document as VisibilityTarget)));

  if (quietDelayMs < 0 || maximumDelayMs < quietDelayMs) {
    throw new RangeError('Persistence delays must satisfy 0 <= quiet <= maximum');
  }
  if (!Number.isSafeInteger(readAttempts) || readAttempts < 1) {
    throw new RangeError('Persistence read attempts must be a positive integer');
  }

  let pending: PendingWrite<S> | null = null;
  let nextGeneration = 0;
  let nextWindowGeneration = 0;
  let durablePayloadKnown = false;
  let durableReadUncertain = false;
  let payloadDirty = false;
  let latestPayloadReferences: JsonObject | null = null;
  let writeChain: Promise<void> = Promise.resolve();
  let latestUnsettledEntry: PendingWrite<S> | null = null;
  let deadlineFallbackFlushes = 0;
  let lifecycleCleanup: (() => void) | null = null;
  let writesSuspended = false;
  let epoch = 0;
  let storageName: string | null = null;
  let nextRevision = 0;
  let pendingDurableClear = false;
  let fallbackWriteError: unknown = null;
  const warnedFailures = new Set<string>();

  function warnFailure(operation: string, error: unknown): void {
    const errorClass = safeErrorClass(error);
    const warningId = `${operation}\u0000${errorClass}`;
    if (warnedFailures.has(warningId)) return;
    warnedFailures.add(warningId);
    warn(`[persistence] ${operation} failed for ${LONGFORM_DB_NAME} (${errorClass})`);
  }

  function flushLocalOrThrow(errorName: string): void {
    const summary = options.flushLocalStorage?.();
    if (summary && summary.failed > 0) throw new DOMException('', errorName);
  }

  function clearPendingTimers(entry: PendingWrite<S>): void {
    if (entry.quietTimer !== null) clearTimer(entry.quietTimer);
    if (entry.maximumTimer !== null) clearTimer(entry.maximumTimer);
    entry.quietTimer = null;
    entry.maximumTimer = null;
  }

  function discardPendingWrites(): boolean {
    if (!pending) return false;
    clearPendingTimers(pending);
    pending = null;
    return true;
  }

  async function commit(entry: PendingWrite<S>, commitEpoch: number): Promise<void> {
    if (commitEpoch !== epoch || getRole() !== 'main') return;
    const revision = entry.revision;
    const record: DurableLongformRecord = {
      schema: LONGFORM_DB_SCHEMA,
      revision,
      payload: extractLongformPayload(entry.value.state),
    };

    try {
      if (pendingDurableClear) {
        await resolveDurableStore().clear();
        pendingDurableClear = false;
      }
      await resolveDurableStore().write(record);
    } catch (error) {
      warnFailure('write', error);
      if (commitEpoch !== epoch || getRole() !== 'main' || entry.generation !== nextGeneration) {
        return;
      }
      payloadDirty = true;
      // IndexedDB may be disabled by policy. Preserve the old full-envelope
      // fallback instead of silently dropping persistence altogether.
      try {
        await options.localStorage.setItem(
          entry.name,
          fullEnvelope(entry.value, revision, pendingDurableClear),
        );
        // The normal localStorage lifecycle listener may already have run before
        // this async IndexedDB rejection. Force its newly queued fallback out now.
        flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_WRITE_ERROR);
        fallbackWriteError = null;
      } catch (localError) {
        fallbackWriteError = localError;
        warnFailure('fallback-write', localError);
      }
      return;
    }

    if (commitEpoch !== epoch || getRole() !== 'main') return;
    durablePayloadKnown = true;
    const isLatestEntry =
      samePayloadReferences(latestPayloadReferences, entry.payloadReferences) &&
      entry.generation === nextGeneration &&
      pending === null;
    if (!isLatestEntry) return;
    fallbackWriteError = null;
    payloadDirty = false;
    try {
      options.localStorage.setItem(entry.name, compactEnvelope(entry.value));
      // A lifecycle flush of the coalesced adapter may already have run before
      // this asynchronous IndexedDB commit completed.
      options.flushLocalStorage?.();
    } catch (error) {
      // The durable payload is already committed. A stale/full local envelope
      // remains a safe fallback and will be compacted on a later hydration.
      warnFailure('compact', error);
    }
  }

  async function flushPendingWrites(): Promise<void> {
    if (getRole() !== 'main') return await writeChain;

    // Include input queued during an IndexedDB commit instead of returning with
    // a dirty timer. A concurrent timer flush may take the entry first;
    // observing writeChain by identity makes us wait for that task as well.
    while (true) {
      if (pending !== null) {
        const entry = pending;
        clearPendingTimers(entry);
        pending = null;
        const commitEpoch = epoch;
        latestUnsettledEntry = entry;
        const task = writeChain.then(async () => {
          try {
            await commit(entry, commitEpoch);
          } finally {
            if (latestUnsettledEntry === entry) latestUnsettledEntry = null;
          }
        });
        writeChain = task.catch(() => {});
      }

      const observedChain = writeChain;
      await observedChain;
      if (getRole() !== 'main') return;
      if (pending === null && observedChain === writeChain) {
        if (fallbackWriteError !== null) {
          if (!options.flushLocalStorage) throw fallbackWriteError;
          try {
            flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_WRITE_ERROR);
            fallbackWriteError = null;
          } catch (error) {
            fallbackWriteError = error;
            throw error;
          }
        }
        return;
      }
    }
  }

  function materializeDeadlineFallback(entry: PendingWrite<S>): void {
    if (writesSuspended) return;
    try {
      options.localStorage.setItem(
        entry.name,
        fullEnvelope(entry.value, entry.revision, pendingDurableClear),
      );
      flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_WRITE_ERROR);
      fallbackWriteError = null;
    } catch (error) {
      fallbackWriteError = error;
      warnFailure('fallback-write', error);
    }
  }

  async function flushPendingWritesForExit(): Promise<void> {
    if (getRole() !== 'main') return await flushPendingWrites();
    deadlineFallbackFlushes += 1;
    try {
      const unsettled = writesSuspended ? null : (pending ?? latestUnsettledEntry);
      if (unsettled !== null) materializeDeadlineFallback(unsettled);
      await flushPendingWrites();
    } finally {
      deadlineFallbackFlushes -= 1;
    }
  }

  function schedule(value: StorageValue<S>, name: string, payloadReferences: JsonObject): void {
    const generation = ++nextGeneration;
    const revision = ++nextRevision;
    const previous = pending;
    if (previous && previous.quietTimer !== null) clearTimer(previous.quietTimer);

    if (previous) {
      pending = {
        name,
        value,
        payloadReferences,
        generation,
        revision,
        windowGeneration: previous.windowGeneration,
        quietTimer: setTimer(() => {
          if (pending?.generation === generation) void flushPendingWrites().catch(() => {});
        }, quietDelayMs),
        maximumTimer: previous.maximumTimer,
      };
      if (deadlineFallbackFlushes > 0) materializeDeadlineFallback(pending);
      return;
    }

    const windowGeneration = ++nextWindowGeneration;
    const firstQueuedAt = now();
    const entry: PendingWrite<S> = {
      name,
      value,
      payloadReferences,
      generation,
      revision,
      windowGeneration,
      quietTimer: null,
      maximumTimer: null,
    };
    entry.quietTimer = setTimer(() => {
      if (pending?.generation === generation) void flushPendingWrites().catch(() => {});
    }, quietDelayMs);
    entry.maximumTimer = setTimer(
      () => {
        if (pending?.windowGeneration === windowGeneration) {
          void flushPendingWrites().catch(() => {});
        }
      },
      Math.max(0, firstQueuedAt + maximumDelayMs - now()),
    );
    pending = entry;
    if (deadlineFallbackFlushes > 0) materializeDeadlineFallback(entry);
  }

  async function readLocal(name: string): Promise<StorageValue<S> | null> {
    try {
      return await options.localStorage.getItem(name);
    } catch (error) {
      warnFailure('local-read', error);
      return null;
    }
  }

  async function hydrate(name: string): Promise<StorageValue<S> | null> {
    storageName = name;
    if (pending?.name === name) return pending.value;

    let localValue = await readLocal(name);
    if (
      localValue &&
      (localValue as VersionedStorageValue<S>)[PENDING_DURABLE_CLEAR_FIELD] === true
    ) {
      const pendingStorageRemove =
        (localValue as VersionedStorageValue<S>)[PENDING_STORAGE_REMOVE_FIELD] === true;
      pendingDurableClear = true;
      const resetLocal = withoutPendingClear(localValue);
      if (getRole() === 'main') {
        try {
          await resolveDurableStore().clear();
          if (pendingStorageRemove) {
            try {
              await options.localStorage.removeItem(name);
              flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR);
              pendingDurableClear = false;
              localValue = null;
            } catch (error) {
              // Keep the empty remove tombstone so the physical deletion is
              // retried instead of restoring bounded preferences.
              warnFailure('clear-tombstone-trim', error);
            }
          } else {
            pendingDurableClear = false;
            localValue = resetLocal;
            try {
              await options.localStorage.setItem(name, resetLocal);
              flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR);
            } catch (error) {
              // The physical tombstone remains authoritative and will retry on the
              // next launch; the inaccessible project payload was still cleared.
              warnFailure('clear-tombstone-trim', error);
            }
          }
        } catch (error) {
          warnFailure('clear', error);
          latestPayloadReferences = pickPayloadReferences(asObject(localValue.state));
          durablePayloadKnown = false;
          durableReadUncertain = false;
          payloadDirty = false;
          return {
            ...localValue,
            state: {
              ...asObject(localValue.state),
              [HYDRATION_ERROR_FIELD]: false,
            } as S,
          };
        }
      }
      if (pendingDurableClear && localValue) {
        return {
          ...localValue,
          state: {
            ...asObject(localValue.state),
            [HYDRATION_ERROR_FIELD]: false,
          } as S,
        };
      }
    }
    let durableRecord: DurableLongformRecord | null = null;
    let readFailed = false;
    let readError: unknown;
    for (let attempt = 0; attempt < readAttempts; attempt += 1) {
      try {
        durableRecord = await resolveDurableStore().read();
        readFailed = false;
        readError = undefined;
        break;
      } catch (error) {
        readFailed = true;
        readError = error;
        if (attempt + 1 < readAttempts) await Promise.resolve();
      }
    }

    if (readFailed) {
      warnFailure('read', readError);
      durablePayloadKnown = false;
      durableReadUncertain = true;
      payloadDirty = false;
      latestPayloadReferences = localValue
        ? pickPayloadReferences(asObject(localValue.state))
        : null;
      if (getRole() === 'readonly') return localValue;
      const unavailable = localValue ?? {
        state: {} as S,
        version: options.currentVersion,
      };
      return {
        ...unavailable,
        state: {
          ...asObject(unavailable.state),
          [HYDRATION_ERROR_FIELD]: true,
        } as S,
      };
    }

    durableReadUncertain = false;
    const localRevision = fallbackRevision(localValue);
    const storedRevision = durableRevision(durableRecord);
    nextRevision = Math.max(nextRevision, localRevision, storedRevision);

    if (
      durableRecord &&
      localValue &&
      containsLongformPayload(localValue.state) &&
      localRevision >= storedRevision
    ) {
      const payload = extractLongformPayload(localValue.state);
      latestPayloadReferences = pickPayloadReferences(asObject(localValue.state));
      durablePayloadKnown = false;
      payloadDirty = true;
      if (getRole() === 'main') {
        try {
          await resolveDurableStore().write({
            schema: LONGFORM_DB_SCHEMA,
            revision: localRevision,
            payload,
          });
          durablePayloadKnown = true;
          payloadDirty = false;
          try {
            options.localStorage.setItem(name, compactEnvelope(localValue));
          } catch (error) {
            warnFailure('compact', error);
          }
        } catch (error) {
          warnFailure('migrate', error);
        }
      }
      return {
        ...localValue,
        state: {
          ...asObject(localValue.state),
          [HYDRATION_ERROR_FIELD]: false,
        } as S,
      };
    }

    if (durableRecord) {
      durablePayloadKnown = true;
      payloadDirty = false;
      latestPayloadReferences = pickPayloadReferences(durableRecord.payload);

      const merged: StorageValue<S> = localValue
        ? {
            ...localValue,
            state: mergePayload(localValue.state, durableRecord.payload) as S,
          }
        : {
            state: durableRecord.payload as S,
            version: options.currentVersion,
          };

      // A prior quota failure may have left the v8/full fallback in place.
      // The IndexedDB commit above is already durable, so trimming is safe now.
      if (getRole() === 'main' && localValue && containsLongformPayload(localValue.state)) {
        try {
          options.localStorage.setItem(name, compactEnvelope(merged));
        } catch (error) {
          warnFailure('compact', error);
        }
      }
      return {
        ...merged,
        state: {
          ...asObject(merged.state),
          [HYDRATION_ERROR_FIELD]: false,
        } as S,
      };
    }

    if (!localValue) {
      return {
        state: { [HYDRATION_ERROR_FIELD]: false } as S,
        version: options.currentVersion,
      };
    }
    const state = asObject(localValue.state);
    const payloadReferences = pickPayloadReferences(state);
    latestPayloadReferences = payloadReferences;

    if (containsLongformPayload(state) && getRole() === 'main') {
      const record: DurableLongformRecord = {
        schema: LONGFORM_DB_SCHEMA,
        revision: localRevision || ++nextRevision,
        payload: extractLongformPayload(state),
      };
      try {
        // The migration's load-bearing ordering: commit IndexedDB first. The
        // persist middleware may compact/version-bump only after this resolves.
        await resolveDurableStore().write(record);
        durablePayloadKnown = true;
        payloadDirty = false;
        if (localValue.version === options.currentVersion) {
          try {
            options.localStorage.setItem(name, compactEnvelope(localValue));
          } catch (error) {
            warnFailure('compact', error);
          }
        }
      } catch (error) {
        durablePayloadKnown = false;
        payloadDirty = true;
        warnFailure('migrate', error);
      }
    }
    return {
      ...localValue,
      state: {
        ...asObject(localValue.state),
        [HYDRATION_ERROR_FIELD]: false,
      } as S,
    };
  }

  const storage: PersistStorage<S> = {
    getItem: hydrate,
    setItem(name, value) {
      storageName = name;
      if (getRole() !== 'main' || writesSuspended) return;
      // A failed read leaves the in-memory long-form fields at slice defaults.
      // The main window is gated until a successful rehydrate reconciles them;
      // never let those placeholders become authoritative in the meantime.
      if (durableReadUncertain) return;
      const state = asObject(value.state);
      const references = pickPayloadReferences(state);
      const payloadChanged = !samePayloadReferences(latestPayloadReferences, references);
      latestPayloadReferences = references;
      if (!durablePayloadKnown || payloadChanged) payloadDirty = true;

      if (durablePayloadKnown && !payloadDirty) {
        try {
          options.localStorage.setItem(name, compactEnvelope(value));
        } catch (error) {
          warnFailure('compact', error);
        }
      }
      if (payloadDirty || pending !== null) schedule(value, name, references);
    },
    removeItem(name) {
      storageName = name;
      if (getRole() !== 'main') return;
      return removeDurableWithTombstone();
    },
  };

  async function removeDurableWithTombstone(): Promise<void> {
    await clearDurableState(true);
  }

  async function clearDurable(): Promise<void> {
    await clearDurableState(false);
  }

  async function clearDurableState(removeLocal: boolean): Promise<void> {
    if (!storageName) throw new DOMException('', 'InvalidStateError');
    const name = storageName;
    const latestValue = pending?.value ?? latestUnsettledEntry?.value ?? null;
    writesSuspended = true;
    discardPendingWrites();
    // Fence an in-flight transaction before recording deletion intent. Its
    // success and failure paths both check this epoch before touching local
    // fallback state, while the early tombstone survives a native timeout.
    epoch += 1;
    latestUnsettledEntry = null;

    let localValue: StorageValue<S> | null = null;
    let localReadSucceeded = false;
    try {
      // Make the physical local envelope truthful before taking a reset snapshot.
      // The normal Zustand adapter deliberately hides read/parse failures; this
      // reset path must propagate them before touching the only IndexedDB copy.
      flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR);
      localValue = await readDurableLocalStorage(name);
      localReadSucceeded = true;

      // Persist the deletion intent before clearing IndexedDB. If IndexedDB is
      // blocked by policy, this tombstone keeps old projects hidden and retries
      // the physical clear on a later launch instead of trapping the user in the
      // recovery gate or resurrecting data after an explicit reset.
      const base =
        latestValue ??
        localValue ??
        ({ state: {} as S, version: options.currentVersion } as StorageValue<S>);
      await options.localStorage.setItem(
        name,
        removeLocal ? pendingRemoveEnvelope<S>(options.currentVersion) : pendingClearEnvelope(base),
      );
      flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR);
    } catch (error) {
      writesSuspended = false;
      if (latestValue) {
        // The reset did not become durable, so restore the fenced generation.
        // It must remain visible to pagehide/native-exit fallback immediately
        // and will retry IndexedDB after any older uncancellable transaction.
        const references = pickPayloadReferences(asObject(latestValue.state));
        latestPayloadReferences = references;
        payloadDirty = true;
        schedule(latestValue, name, references);
        if (pending) materializeDeadlineFallback(pending);
      } else if (localReadSucceeded) {
        // The physical tombstone may have succeeded before an aggregate local
        // flush reported another key's failure. Restore the exact pre-reset
        // envelope (including absence) before reporting that reset failed.
        try {
          if (localValue) await options.localStorage.setItem(name, localValue);
          else await options.localStorage.removeItem(name);
          flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR);
        } catch (rollbackError) {
          warnFailure('clear-rollback', rollbackError);
        }
      }
      warnFailure('clear-local', error);
      throw error;
    }

    pendingDurableClear = true;
    durablePayloadKnown = false;
    durableReadUncertain = false;
    payloadDirty = false;
    latestPayloadReferences = null;
    fallbackWriteError = null;
    nextRevision = 0;

    // The physical deletion intent is now safe. Wait for the uncancellable
    // stale transaction, then clear anything it may have committed.
    await writeChain;
    try {
      await resolveDurableStore().clear();
      pendingDurableClear = false;
    } catch (error) {
      pendingDurableClear = true;
      warnFailure('clear', error);
      return;
    }

    if (removeLocal) {
      try {
        await options.localStorage.removeItem(name);
        flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR);
      } catch (error) {
        pendingDurableClear = true;
        warnFailure('clear-tombstone-trim', error);
        throw error;
      }
      return;
    }

    try {
      await options.localStorage.setItem(
        name,
        resetEnvelope(latestValue ?? localValue ?? { state: {} as S }),
      );
      flushLocalOrThrow(LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR);
    } catch (error) {
      // The already-durable tombstone is a safe completed reset. Leaving it in
      // place merely causes one idempotent clear retry on the next startup.
      warnFailure('clear-tombstone-trim', error);
    }
  }

  function installLifecycleFlush(): () => void {
    if (getRole() !== 'main') return () => {};
    if (lifecycleCleanup) return lifecycleCleanup;

    const pagehideTarget = getPagehideTarget();
    const visibilityTarget = getVisibilityTarget();
    const onPagehide: EventListener = () => {
      // A browser page cannot keep IndexedDB alive while closing. Materialize
      // the full synchronous fallback before the first asynchronous wait.
      void flushPendingWritesForExit().catch(() => {});
    };
    const onVisibilityChange: EventListener = () => {
      if (visibilityTarget?.visibilityState === 'hidden') {
        void flushPendingWrites().catch(() => {});
      }
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

  function reset(): void {
    epoch += 1;
    discardPendingWrites();
    lifecycleCleanup?.();
    lifecycleCleanup = null;
    durablePayloadKnown = false;
    durableReadUncertain = false;
    payloadDirty = false;
    writesSuspended = false;
    latestPayloadReferences = null;
    latestUnsettledEntry = null;
    storageName = null;
    nextRevision = 0;
    pendingDurableClear = false;
    fallbackWriteError = null;
    deadlineFallbackFlushes = 0;
    nextGeneration = 0;
    nextWindowGeneration = 0;
    warnedFailures.clear();
    writeChain = Promise.resolve();
  }

  return {
    storage,
    flushPendingWrites,
    flushPendingWritesForExit,
    discardPendingWrites,
    clearDurable,
    installLifecycleFlush,
    reset,
  };
}

const nativeDurableStore = createIndexedDbLongformStore();
let applicationDurableStore: LongformDurableStore = nativeDurableStore;
const applicationPersistence = createLongformPersistence<JsonObject>({
  localStorage: createZustandJsonStorage<JsonObject>(),
  durableStore: () => applicationDurableStore,
  currentVersion: 9,
  getRole: getPersistenceRole,
  flushLocalStorage: flushLocalPendingWrites,
  readDurableLocalStorage: (name) => readDurableJsonValue<StorageValue<JsonObject>>(name),
});

export function createLongformZustandStorage<S>(): PersistStorage<S> {
  return applicationPersistence.storage as PersistStorage<S>;
}

export const flushLongformPendingWrites = applicationPersistence.flushPendingWrites;
export const flushLongformPendingWritesForExit = applicationPersistence.flushPendingWritesForExit;
export const discardLongformPendingWrites = applicationPersistence.discardPendingWrites;
export const installLongformPersistenceLifecycleFlush =
  applicationPersistence.installLifecycleFlush;

/**
 * Clear browser-owned projects for Settings' explicit destructive content reset.
 * `clearDurable` flushes pending local writes, durably records deletion intent,
 * and only then clears IndexedDB; its successful return needs no second flush.
 */
export async function clearLongformProjects(): Promise<void> {
  await applicationPersistence.clearDurable();
}

/** Inject a deterministic durable store without replacing the Zustand adapter. */
export function configureLongformDurableStoreForTests(store: LongformDurableStore): void {
  applicationPersistence.reset();
  applicationDurableStore = store;
}

/** Test/HMR teardown. Never clears IndexedDB. */
export function resetLongformPersistenceForTests(): void {
  applicationPersistence.reset();
  applicationDurableStore = nativeDurableStore;
}
