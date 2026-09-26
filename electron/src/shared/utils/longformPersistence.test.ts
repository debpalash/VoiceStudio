import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PersistStorage, StorageValue } from 'zustand/middleware';

import {
  createLongformPersistence,
  LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR,
  LONGFORM_LOCAL_FALLBACK_WRITE_ERROR,
  type DurableLongformRecord,
  type LongformDurableStore,
} from './longformPersistence';
import { createCoalescedJsonStorage } from './coalescedJsonStorage';

type TestState = Record<string, unknown>;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function createLocalStorage(initial: StorageValue<TestState> | null = null, quota = false) {
  let value = initial ? clone(initial) : null;
  const storage: PersistStorage<TestState> = {
    getItem: vi.fn(() => (value ? clone(value) : null)),
    setItem: vi.fn((_name, next) => {
      if (quota) throw new DOMException('content intentionally omitted', 'QuotaExceededError');
      value = clone(next);
    }),
    removeItem: vi.fn(() => {
      value = null;
    }),
  };
  return {
    storage,
    read: () => (value ? clone(value) : null),
    replace: (next: StorageValue<TestState> | null) => {
      value = next ? clone(next) : null;
    },
  };
}

function createDurableStore(initial: DurableLongformRecord | null = null) {
  let value = initial ? clone(initial) : null;
  const store: LongformDurableStore = {
    read: vi.fn(async () => (value ? clone(value) : null)),
    write: vi.fn(async (next) => {
      value = clone(next);
    }),
    clear: vi.fn(async () => {
      value = null;
    }),
  };
  return { store, read: () => (value ? clone(value) : null) };
}

function legacyEnvelope(script: string): StorageValue<TestState> {
  return {
    version: 8,
    state: {
      theme: 'dark',
      currentProjectId: 'p_book',
      storyTracks: [
        {
          id: 1,
          character: 'narrator',
          text: script,
          profileId: null,
          emotion: null,
          speed: null,
          generating: true,
          audioUrl: 'blob:runtime-only',
        },
      ],
      cast: [{ id: 'narrator', name: 'Narrator', color: '#fabd2f', profileId: null }],
      storyProjects: [{ id: 'p_book', name: 'Book', script, tracks: [], cast: [] }],
      script,
      meta: { title: 'Book' },
      lexicon: { VoiceStudio: 'voice studio' },
      voiceCast: {},
    },
  };
}

describe('split long-form persistence', () => {
  beforeEach(() => vi.useFakeTimers());

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('commits a v8 payload before trimming the legacy localStorage envelope', async () => {
    const events: string[] = [];
    const local = createLocalStorage(legacyEnvelope('legacy manuscript'));
    const durable = createDurableStore();
    vi.mocked(durable.store.write).mockImplementation(async (record) => {
      events.push('indexeddb');
      await Promise.resolve();
      (durable.store.write as any).record = clone(record);
    });
    vi.mocked(local.storage.setItem).mockImplementation((_name, value) => {
      events.push('localStorage');
      (local.storage.setItem as any).value = clone(value);
    });
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });

    const hydrated = await controller.storage.getItem('omnivoice.app');
    expect(hydrated?.state.script).toBe('legacy manuscript');
    expect(events).toEqual(['indexeddb']);

    controller.storage.setItem('omnivoice.app', { ...hydrated!, version: 9 });
    expect(events).toEqual(['indexeddb', 'localStorage']);
    const compact = (local.storage.setItem as any).value;
    expect(compact.state).not.toHaveProperty('script');
    expect(compact.state).not.toHaveProperty('storyProjects');
  });

  it('round-trips a large project while keeping localStorage bounded', async () => {
    const largeScript = 'A long chapter. '.repeat(400_000);
    const envelope = legacyEnvelope(largeScript);
    envelope.version = 9;
    const local = createLocalStorage();
    const durable = createDurableStore();
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });

    await controller.storage.getItem('omnivoice.app');
    controller.storage.setItem('omnivoice.app', envelope);
    await vi.advanceTimersByTimeAsync(250);
    await controller.flushPendingWrites();

    const compact = local.read();
    const persisted = durable.read();
    expect(compact).not.toBeNull();
    expect(JSON.stringify(compact).length).toBeLessThan(100_000);
    expect(compact?.state).not.toHaveProperty('script');
    expect(persisted?.payload.script).toBe(largeScript);
    expect(persisted).not.toBeNull();
    expect((persisted!.payload.storyTracks as TestState[])[0]).not.toHaveProperty('generating');
    expect((persisted!.payload.storyTracks as TestState[])[0]).not.toHaveProperty('audioUrl');

    const reloaded = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });
    const restored = await reloaded.storage.getItem('omnivoice.app');
    expect(restored?.state.script).toBe(largeScript);
    expect(restored).not.toBeNull();
    expect((restored!.state.storyProjects as TestState[])[0].script).toBe(largeScript);
  });

  it('recovers from localStorage quota failure using the committed durable payload', async () => {
    const largeScript = 'quota-safe manuscript '.repeat(300_000);
    const legacy = legacyEnvelope(largeScript);
    const local = createLocalStorage(legacy, true);
    const durable = createDurableStore();
    const warnings: string[] = [];
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      warn: (warning) => warnings.push(warning),
    });

    const hydrated = await controller.storage.getItem('omnivoice.app');
    controller.storage.setItem('omnivoice.app', { ...hydrated!, version: 9 });

    expect(local.read()).toEqual(legacy);
    expect(durable.read()?.payload.script).toBe(largeScript);
    expect(warnings).toEqual([
      '[persistence] compact failed for omnivoice.longform (QuotaExceededError)',
    ]);

    const reloaded = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      warn: () => {},
    });
    const restored = await reloaded.storage.getItem('omnivoice.app');
    expect(restored?.state.script).toBe(largeScript);
    expect(restored).not.toBeNull();
    expect((restored!.state.storyProjects as TestState[])[0].name).toBe('Book');
  });

  it('retains the full legacy fallback when IndexedDB is out of quota', async () => {
    const legacy = legacyEnvelope('last recoverable copy');
    const local = createLocalStorage(legacy);
    const durable = createDurableStore();
    vi.mocked(durable.store.write).mockRejectedValue(
      new DOMException('content intentionally omitted', 'QuotaExceededError'),
    );
    const warnings: string[] = [];
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      warn: (warning) => warnings.push(warning),
    });

    const hydrated = await controller.storage.getItem('omnivoice.app');
    expect(local.read()).toEqual(legacy);
    expect(local.storage.setItem).not.toHaveBeenCalled();

    // Zustand's post-hydration v9 write retries IndexedDB, then falls back to
    // the still-full local envelope instead of trimming the only durable copy.
    controller.storage.setItem('omnivoice.app', { ...hydrated!, version: 9 });
    await vi.advanceTimersByTimeAsync(250);
    await controller.flushPendingWrites();

    expect(local.read()?.state.script).toBe('last recoverable copy');
    expect(local.read()?.state).toHaveProperty('storyProjects');
    expect(warnings).toEqual([
      '[persistence] migrate failed for omnivoice.longform (QuotaExceededError)',
      '[persistence] write failed for omnivoice.longform (QuotaExceededError)',
    ]);
  });

  it('recovers a newer full fallback instead of compacting it behind stale IndexedDB', async () => {
    const local = createLocalStorage();
    const durable = createDurableStore();
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      warn: () => {},
    });
    const first = legacyEnvelope('durable A');
    first.version = 9;
    const second = legacyEnvelope('fallback B');
    second.version = 9;

    await controller.storage.getItem('omnivoice.app');
    controller.storage.setItem('omnivoice.app', first);
    await controller.flushPendingWrites();
    expect(durable.read()?.payload.script).toBe('durable A');

    vi.mocked(durable.store.write).mockRejectedValueOnce(
      new DOMException('content intentionally omitted', 'QuotaExceededError'),
    );
    controller.storage.setItem('omnivoice.app', second);
    await controller.flushPendingWrites();
    expect(local.read()?.state.script).toBe('fallback B');
    expect(durable.read()?.payload.script).toBe('durable A');

    const reloaded = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      warn: () => {},
    });
    const restored = await reloaded.storage.getItem('omnivoice.app');

    expect(restored?.state.script).toBe('fallback B');
    expect(durable.read()?.payload.script).toBe('fallback B');
    expect(local.read()?.state).not.toHaveProperty('script');
  });

  it('prefers a later full fallback when another tab reused the durable revision', async () => {
    const localValue = legacyEnvelope('later fallback B') as StorageValue<TestState> & {
      longformFallbackRevision: number;
    };
    localValue.version = 9;
    localValue.longformFallbackRevision = 6;
    const local = createLocalStorage(localValue);
    const durable = createDurableStore({
      schema: 1,
      revision: 6,
      payload: legacyEnvelope('earlier durable A').state,
    });
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });

    const restored = await controller.storage.getItem('omnivoice.app');

    expect(restored?.state.script).toBe('later fallback B');
    expect(durable.read()?.payload.script).toBe('later fallback B');
  });

  it('retries a transient IndexedDB read before exposing project state', async () => {
    const local = createLocalStorage();
    const durable = createDurableStore();
    const initial = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });
    const persisted = legacyEnvelope('durable manuscript');
    persisted.version = 9;
    await initial.storage.getItem('omnivoice.app');
    initial.storage.setItem('omnivoice.app', persisted);
    await initial.flushPendingWrites();
    vi.mocked(durable.store.read).mockClear();
    vi.mocked(durable.store.read).mockRejectedValueOnce(
      new DOMException('content intentionally omitted', 'UnknownError'),
    );

    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      warn: () => {},
    });
    const restored = await controller.storage.getItem('omnivoice.app');

    expect(restored?.state.script).toBe('durable manuscript');
    expect(restored?.state.longformPersistenceError).toBe(false);
    expect(durable.store.read).toHaveBeenCalledTimes(2);
  });

  it('gates writes instead of replacing durable projects after persistent read errors', async () => {
    const local = createLocalStorage();
    const durable = createDurableStore();
    const initial = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });
    const persisted = legacyEnvelope('durable manuscript');
    persisted.version = 9;
    await initial.storage.getItem('omnivoice.app');
    initial.storage.setItem('omnivoice.app', persisted);
    await initial.flushPendingWrites();
    vi.mocked(durable.store.write).mockClear();
    vi.mocked(durable.store.read).mockRejectedValue(
      new DOMException('content intentionally omitted', 'UnknownError'),
    );

    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      readAttempts: 2,
      warn: () => {},
    });
    const unavailable = await controller.storage.getItem('omnivoice.app');
    expect(unavailable?.state).not.toHaveProperty('script');
    expect(unavailable?.state.longformPersistenceError).toBe(true);

    const unrelatedUpdate = legacyEnvelope('fresh defaults');
    unrelatedUpdate.version = 9;
    unrelatedUpdate.state.theme = 'light';
    controller.storage.setItem('omnivoice.app', unrelatedUpdate);
    await controller.flushPendingWrites();

    expect(durable.store.write).not.toHaveBeenCalled();
    expect(durable.read()?.payload.script).toBe('durable manuscript');

    vi.mocked(durable.store.read).mockImplementation(async () => durable.read());
    const reloaded = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });
    const restored = await reloaded.storage.getItem('omnivoice.app');
    expect(restored?.state.script).toBe('durable manuscript');
  });

  it('treats an undefined IndexedDB rejection as a failed read', async () => {
    const local = createLocalStorage();
    const write = vi.fn(async () => {});
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => {
        throw undefined;
      }),
      write,
      clear: vi.fn(async () => {}),
    };
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      readAttempts: 1,
      warn: () => {},
    });

    const unavailable = await controller.storage.getItem('omnivoice.app');
    expect(unavailable?.state.longformPersistenceError).toBe(true);

    controller.storage.setItem('omnivoice.app', legacyEnvelope('must not overwrite'));
    await controller.flushPendingWrites();
    expect(write).not.toHaveBeenCalled();
  });

  it('lets an explicit reset recover when IndexedDB is permanently unavailable', async () => {
    const local = createLocalStorage();
    const blocked = new DOMException('private content', 'SecurityError');
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => {
        throw blocked;
      }),
      write: vi.fn(async () => {
        throw blocked;
      }),
      clear: vi.fn(async () => {
        throw blocked;
      }),
    };
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      readAttempts: 1,
      warn: () => {},
    });

    const unavailable = await controller.storage.getItem('omnivoice.app');
    expect(unavailable?.state.longformPersistenceError).toBe(true);
    await expect(controller.clearDurable()).resolves.toBeUndefined();
    expect(local.read()).toMatchObject({
      longformPendingDurableClear: true,
      state: { currentProjectId: null },
    });

    const reloaded = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      readAttempts: 1,
      warn: () => {},
    });
    const recovered = await reloaded.storage.getItem('omnivoice.app');
    expect(recovered?.state.longformPersistenceError).toBe(false);
    expect(recovered?.state).not.toHaveProperty('script');
  });

  it('keeps a deletion tombstone when clearStorage cannot clear IndexedDB', async () => {
    const localValue = legacyEnvelope('must stay deleted');
    localValue.version = 9;
    const local = createLocalStorage(localValue);
    let blocked = true;
    let durableValue: DurableLongformRecord | null = {
      schema: 1,
      revision: 3,
      payload: localValue.state,
    };
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => clone(durableValue)),
      write: vi.fn(async (record) => {
        durableValue = clone(record);
      }),
      clear: vi.fn(async () => {
        if (blocked) throw new DOMException('blocked', 'SecurityError');
        durableValue = null;
      }),
    };
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      warn: () => {},
    });
    await controller.storage.getItem('omnivoice.app');

    await controller.storage.removeItem('omnivoice.app');

    expect(local.read()).toMatchObject({
      longformPendingDurableClear: true,
      longformPendingStorageRemove: true,
      state: {},
    });
    expect(local.read()?.state).not.toHaveProperty('theme');
    expect(durableValue?.payload.script).toBe('must stay deleted');

    const reloaded = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      warn: () => {},
    });
    const restored = await reloaded.storage.getItem('omnivoice.app');
    expect(restored?.state).not.toHaveProperty('script');
    expect(restored?.state).not.toHaveProperty('theme');
    expect(restored?.state.longformPersistenceError).toBe(false);

    blocked = false;
    const recovered = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
    });
    await recovered.storage.getItem('omnivoice.app');
    expect(durableValue).toBeNull();
    expect(local.read()).toBeNull();
  });

  it('removes the local envelope only after clearStorage clears IndexedDB', async () => {
    const localValue = legacyEnvelope('delete successfully');
    localValue.version = 9;
    const local = createLocalStorage(localValue);
    const durable = createDurableStore({ schema: 1, revision: 2, payload: localValue.state });
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });
    await controller.storage.getItem('omnivoice.app');

    await controller.storage.removeItem('omnivoice.app');

    expect(durable.read()).toBeNull();
    expect(local.read()).toBeNull();
  });

  it('removes a pending-clear tombstone after IndexedDB recovers', async () => {
    const tombstone = {
      version: 9,
      longformPendingDurableClear: true,
      state: { currentProjectId: null },
    } as StorageValue<TestState>;
    const local = createLocalStorage(tombstone);
    let unavailable = true;
    let durableValue: DurableLongformRecord | null = {
      schema: 1,
      revision: 4,
      payload: legacyEnvelope('previously inaccessible').state,
    };
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => clone(durableValue)),
      write: vi.fn(async (record) => {
        durableValue = clone(record);
      }),
      clear: vi.fn(async () => {
        if (unavailable) throw new DOMException('private content', 'SecurityError');
        durableValue = null;
      }),
    };
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      warn: () => {},
    });

    await controller.storage.getItem('omnivoice.app');
    unavailable = false;
    await controller.clearDurable();
    expect(local.read()).not.toHaveProperty('longformPendingDurableClear');

    await durableStore.write({
      schema: 1,
      revision: 1,
      payload: legacyEnvelope('new project after recovery').state,
    });
    const clearCalls = vi.mocked(durableStore.clear).mock.calls.length;
    const reloaded = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
    });
    const restored = await reloaded.storage.getItem('omnivoice.app');

    expect(restored?.state.script).toBe('new project after recovery');
    expect(durableStore.clear).toHaveBeenCalledTimes(clearCalls);
  });

  it('does not clear IndexedDB when the physical local reset snapshot cannot be read', async () => {
    const local = createLocalStorage({ version: 9, state: { theme: 'dark' } });
    const durable = createDurableStore({
      schema: 1,
      revision: 2,
      payload: legacyEnvelope('only durable copy').state,
    });
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      readDurableLocalStorage: () => {
        throw new DOMException('private content', 'SecurityError');
      },
      warn: () => {},
    });
    await controller.storage.getItem('omnivoice.app');

    await expect(controller.clearDurable()).rejects.toHaveProperty('name', 'SecurityError');
    expect(durable.store.clear).not.toHaveBeenCalled();
    expect(durable.read()?.payload.script).toBe('only durable copy');
  });

  it('restores a pending project when clearStorage cannot persist its tombstone', async () => {
    const local = createLocalStorage();
    const durable = createDurableStore();
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      readDurableLocalStorage: () => {
        throw new DOMException('blocked', 'SecurityError');
      },
      warn: () => {},
    });
    controller.storage.setItem('omnivoice.app', legacyEnvelope('pending reset failure'));

    await expect(controller.storage.removeItem('omnivoice.app')).rejects.toHaveProperty(
      'name',
      'SecurityError',
    );

    const exitFlush = controller.flushPendingWritesForExit();
    expect(local.read()?.state.script).toBe('pending reset failure');
    await exitFlush;
    expect(durable.read()?.payload.script).toBe('pending reset failure');
  });

  it('restores an in-flight project when the reset tombstone write fails', async () => {
    let durableValue: DurableLongformRecord | null = null;
    let releaseWrite = () => {};
    let writes = 0;
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => durableValue),
      write: vi.fn(async (record) => {
        writes += 1;
        if (writes === 1) {
          await new Promise<void>((resolve) => {
            releaseWrite = resolve;
          });
        }
        durableValue = clone(record);
      }),
      clear: vi.fn(async () => {
        durableValue = null;
      }),
    };
    const local = createLocalStorage();
    local.storage.setItem = vi.fn((_name, value) => {
      if (
        (value as StorageValue<TestState> & { longformPendingDurableClear?: boolean })
          .longformPendingDurableClear === true
      ) {
        throw new DOMException('blocked', 'QuotaExceededError');
      }
      local.replace(value);
    });
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      warn: () => {},
    });
    controller.storage.setItem('omnivoice.app', legacyEnvelope('in-flight reset failure'));
    const flushing = controller.flushPendingWrites();
    await vi.waitFor(() => expect(durableStore.write).toHaveBeenCalledOnce());

    await expect(controller.clearDurable()).rejects.toHaveProperty('name', 'QuotaExceededError');

    const exitFlush = controller.flushPendingWritesForExit();
    expect(local.read()?.state.script).toBe('in-flight reset failure');
    releaseWrite();
    await Promise.all([flushing, exitFlush]);
    expect((durableValue as DurableLongformRecord | null)?.payload.script).toBe(
      'in-flight reset failure',
    );
  });

  it('restores the physical envelope when another local flush fails after the tombstone', async () => {
    const original = legacyEnvelope('physical reset rollback');
    const local = createLocalStorage(original);
    const durable = createDurableStore();
    let flushes = 0;
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      flushLocalStorage: () => {
        flushes += 1;
        return {
          attempted: 1,
          written: flushes === 2 ? 0 : 1,
          skipped: 0,
          failed: flushes === 2 ? 1 : 0,
          discarded: 0,
        };
      },
      warn: () => {},
    });
    await controller.storage.getItem('omnivoice.app');

    await expect(controller.clearDurable()).rejects.toHaveProperty(
      'name',
      LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR,
    );

    expect(local.read()).toEqual(original);
    expect(durable.read()?.payload.script).toBe('physical reset rollback');
    expect(durable.store.clear).not.toHaveBeenCalled();
  });

  it('removes a failed reset tombstone when localStorage was originally empty', async () => {
    const local = createLocalStorage();
    const durable = createDurableStore({
      schema: 1,
      revision: 2,
      payload: legacyEnvelope('durable reset rollback').state,
    });
    let flushes = 0;
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
      flushLocalStorage: () => {
        flushes += 1;
        return {
          attempted: 1,
          written: flushes === 2 ? 0 : 1,
          skipped: 0,
          failed: flushes === 2 ? 1 : 0,
          discarded: 0,
        };
      },
      warn: () => {},
    });
    await controller.storage.getItem('omnivoice.app');

    await expect(controller.clearDurable()).rejects.toHaveProperty(
      'name',
      LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR,
    );

    expect(local.read()).toBeNull();
    expect(durable.read()?.payload.script).toBe('durable reset rollback');
    expect(durable.store.clear).not.toHaveBeenCalled();
  });

  it('reports a failed full fallback flush instead of acknowledging persistence', async () => {
    const raw = new Map<string, string>([
      ['omnivoice.app', JSON.stringify(legacyEnvelope('last physical copy'))],
    ]);
    const localController = createCoalescedJsonStorage({
      getStorage: () => ({
        getItem: (key) => raw.get(key) ?? null,
        setItem: () => {
          throw new DOMException('content intentionally omitted', 'QuotaExceededError');
        },
        removeItem: (key) => raw.delete(key),
      }),
      warn: () => {},
    });
    localController.configurePersistenceRole('main');
    const durable = createDurableStore();
    vi.mocked(durable.store.write).mockRejectedValue(
      new DOMException('content intentionally omitted', 'QuotaExceededError'),
    );
    const controller = createLongformPersistence({
      localStorage: localController.createZustandJsonStorage(),
      durableStore: durable.store,
      currentVersion: 9,
      flushLocalStorage: () => localController.flushPendingWrites(),
      warn: () => {},
    });
    const hydrated = await controller.storage.getItem('omnivoice.app');
    controller.storage.setItem('omnivoice.app', { ...hydrated!, version: 9 });

    await expect(controller.flushPendingWrites()).rejects.toHaveProperty(
      'name',
      LONGFORM_LOCAL_FALLBACK_WRITE_ERROR,
    );
    expect(JSON.parse(raw.get('omnivoice.app')!).state.script).toBe('last physical copy');
  });

  it('flushes a full local fallback when pagehide already ran the local listener', async () => {
    const raw = new Map<string, string>();
    const localController = createCoalescedJsonStorage({
      getStorage: () => ({
        getItem: (key) => raw.get(key) ?? null,
        setItem: (key, value) => raw.set(key, value),
        removeItem: (key) => raw.delete(key),
      }),
    });
    localController.configurePersistenceRole('main');
    const durable = createDurableStore();
    vi.mocked(durable.store.write).mockRejectedValue(
      new DOMException('content intentionally omitted', 'QuotaExceededError'),
    );
    const controller = createLongformPersistence({
      localStorage: localController.createZustandJsonStorage(),
      durableStore: durable.store,
      currentVersion: 9,
      warn: () => {},
      flushLocalStorage: () => localController.flushPendingWrites(),
    });
    const cleanupLocal = localController.installPersistenceLifecycleFlush();
    const cleanupLongform = controller.installLifecycleFlush();

    await controller.storage.getItem('omnivoice.app');
    const envelope = legacyEnvelope('pagehide fallback');
    envelope.version = 9;
    controller.storage.setItem('omnivoice.app', envelope);
    window.dispatchEvent(new Event('pagehide'));
    await controller.flushPendingWrites();

    expect(JSON.parse(raw.get('omnivoice.app')!).state.script).toBe('pagehide fallback');
    cleanupLongform();
    cleanupLocal();
  });

  it('flushes compact state after a successful async pagehide commit', async () => {
    const raw = new Map<string, string>();
    const localController = createCoalescedJsonStorage({
      getStorage: () => ({
        getItem: (key) => raw.get(key) ?? null,
        setItem: (key, value) => raw.set(key, value),
        removeItem: (key) => raw.delete(key),
      }),
    });
    localController.configurePersistenceRole('main');
    const durable = createDurableStore();
    const controller = createLongformPersistence({
      localStorage: localController.createZustandJsonStorage(),
      durableStore: durable.store,
      currentVersion: 9,
      flushLocalStorage: () => localController.flushPendingWrites(),
    });
    // main-app installs these in this order: the local listener runs before
    // the async IndexedDB listener during the same lifecycle event.
    const cleanupLocal = localController.installPersistenceLifecycleFlush();
    const cleanupLongform = controller.installLifecycleFlush();

    await controller.storage.getItem('omnivoice.app');
    const envelope = legacyEnvelope('successfully committed manuscript');
    envelope.version = 9;
    envelope.state.coverRef = { filename: 'cover.png', serverPath: '/covers/cover.png' };
    envelope.state.lastOutput = 'book.m4b';
    controller.storage.setItem('omnivoice.app', envelope);
    window.dispatchEvent(new Event('pagehide'));
    await controller.flushPendingWrites();

    expect(raw.get('omnivoice.app')).toBeDefined();
    const compact = JSON.parse(raw.get('omnivoice.app')!);
    expect(compact.state).toMatchObject({
      theme: 'dark',
      currentProjectId: 'p_book',
      coverRef: { filename: 'cover.png', serverPath: '/covers/cover.png' },
      lastOutput: 'book.m4b',
    });
    expect(compact.state).not.toHaveProperty('script');
    expect(compact.state).not.toHaveProperty('storyProjects');
    cleanupLongform();
    cleanupLocal();
  });

  it('materializes the pagehide fallback before a blocked IndexedDB write settles', async () => {
    let durableValue: DurableLongformRecord | null = null;
    let releaseWrite = () => {};
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => durableValue),
      write: vi.fn(
        (record) =>
          new Promise<void>((resolve) => {
            releaseWrite = () => {
              durableValue = clone(record);
              resolve();
            };
          }),
      ),
      clear: vi.fn(async () => {
        durableValue = null;
      }),
    };
    const local = createLocalStorage();
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
    });
    const cleanup = controller.installLifecycleFlush();

    controller.storage.setItem('omnivoice.app', legacyEnvelope('blocked pagehide edit'));
    const flushing = controller.flushPendingWrites();
    await vi.waitFor(() => expect(durableStore.write).toHaveBeenCalledOnce());

    window.dispatchEvent(new Event('pagehide'));

    expect(local.read()?.state.script).toBe('blocked pagehide edit');
    releaseWrite();
    await flushing;
    cleanup();
  });

  it('keeps an explicit content reset from being resurrected before reload', async () => {
    const local = createLocalStorage();
    const durable = createDurableStore();
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });
    const envelope = legacyEnvelope('remove me');
    envelope.version = 9;

    controller.storage.setItem('omnivoice.app', envelope);
    await controller.flushPendingWrites();
    expect(durable.read()?.payload.script).toBe('remove me');

    // Simulate a previous compact write failing under quota: Settings must
    // trim this recoverable v8 fallback as well as delete IndexedDB.
    local.replace(legacyEnvelope('legacy fallback'));
    await controller.clearDurable();
    controller.storage.setItem('omnivoice.app', legacyEnvelope('must not return'));
    await vi.runAllTimersAsync();

    expect(durable.read()).toBeNull();
    expect(local.read()?.state).not.toHaveProperty('script');
    expect(local.read()?.state).not.toHaveProperty('storyProjects');
  });

  it('clears bounded references to deleted long-form content before reload', async () => {
    const local = createLocalStorage({
      version: 9,
      state: {
        theme: 'dark',
        currentProjectId: 'p_deleted',
        coverRef: { filename: 'deleted.png', serverPath: '/covers/deleted.png' },
        lastOutput: 'deleted-book.m4b',
        outputFormat: 'm4b',
      },
    });
    const durable = createDurableStore({
      schema: 1,
      payload: legacyEnvelope('delete me').state,
    });
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });

    await controller.storage.getItem('omnivoice.app');
    await controller.clearDurable();

    const reloaded = createLongformPersistence({
      localStorage: local.storage,
      durableStore: durable.store,
      currentVersion: 9,
    });
    const restored = await reloaded.storage.getItem('omnivoice.app');
    expect(restored?.state).toMatchObject({
      theme: 'dark',
      currentProjectId: null,
      coverRef: null,
      lastOutput: '',
      outputFormat: 'm4b',
    });
    expect(restored?.state).not.toHaveProperty('script');
    expect(durable.read()).toBeNull();
  });

  it('waits out an in-flight write before clearing durable projects', async () => {
    let durableValue: DurableLongformRecord | null = null;
    let releaseWrite = () => {};
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => durableValue),
      write: vi.fn(
        (record) =>
          new Promise<void>((resolve) => {
            releaseWrite = () => {
              durableValue = clone(record);
              resolve();
            };
          }),
      ),
      clear: vi.fn(async () => {
        durableValue = null;
      }),
    };
    const controller = createLongformPersistence({
      localStorage: createLocalStorage().storage,
      durableStore,
      currentVersion: 9,
    });

    controller.storage.setItem('omnivoice.app', legacyEnvelope('in flight'));
    const flushing = controller.flushPendingWrites();
    await Promise.resolve();
    expect(durableStore.write).toHaveBeenCalledOnce();

    const clearing = controller.clearDurable();
    expect(durableStore.clear).not.toHaveBeenCalled();
    releaseWrite();
    await flushing;
    await clearing;

    expect(durableStore.clear).toHaveBeenCalledOnce();
    expect(durableValue).toBeNull();
  });

  it('records a reset tombstone before waiting for an in-flight write', async () => {
    let durableValue: DurableLongformRecord | null = null;
    let releaseWrite = () => {};
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => durableValue),
      write: vi.fn(
        (record) =>
          new Promise<void>((resolve) => {
            releaseWrite = () => {
              durableValue = clone(record);
              resolve();
            };
          }),
      ),
      clear: vi.fn(async () => {
        durableValue = null;
      }),
    };
    const local = createLocalStorage();
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
    });

    controller.storage.setItem('omnivoice.app', legacyEnvelope('delete while blocked'));
    const flushing = controller.flushPendingWrites();
    await vi.waitFor(() => expect(durableStore.write).toHaveBeenCalledOnce());

    const clearing = controller.clearDurable();
    await vi.waitFor(() =>
      expect(local.read()).toMatchObject({
        longformPendingDurableClear: true,
      }),
    );
    const exitFlush = controller.flushPendingWritesForExit();
    expect(local.read()?.state).not.toHaveProperty('script');

    const timedOutReload = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      getRole: () => 'readonly',
    });
    const restored = await timedOutReload.storage.getItem('omnivoice.app');
    expect(restored?.state).not.toHaveProperty('script');

    releaseWrite();
    await Promise.all([flushing, clearing, exitFlush]);
    expect(durableValue).toBeNull();
    expect(local.read()?.state).not.toHaveProperty('script');
  });

  it('preserves the latest bounded preferences while resetting an in-flight project', async () => {
    let durableValue: DurableLongformRecord | null = null;
    let releaseWrite = () => {};
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => durableValue),
      write: vi.fn(
        (record) =>
          new Promise<void>((resolve) => {
            releaseWrite = () => {
              durableValue = clone(record);
              resolve();
            };
          }),
      ),
      clear: vi.fn(async () => {
        durableValue = null;
      }),
    };
    const local = createLocalStorage({ version: 9, state: { theme: 'dark' } });
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
    });
    await controller.storage.getItem('omnivoice.app');
    const latest = legacyEnvelope('reset this project');
    latest.version = 9;
    latest.state.theme = 'light';

    controller.storage.setItem('omnivoice.app', latest);
    const flushing = controller.flushPendingWrites();
    await vi.waitFor(() => expect(durableStore.write).toHaveBeenCalledOnce());

    const clearing = controller.clearDurable();
    await vi.waitFor(() =>
      expect(local.read()).toMatchObject({
        longformPendingDurableClear: true,
        state: { theme: 'light' },
      }),
    );
    releaseWrite();
    await Promise.all([flushing, clearing]);

    expect(durableValue).toBeNull();
    expect(local.read()?.state.theme).toBe('light');
    expect(local.read()?.state).not.toHaveProperty('script');
  });

  it('keeps an orderly flush open for edits queued during its IndexedDB commit', async () => {
    let durableValue: DurableLongformRecord | null = null;
    let releaseFirstWrite = () => {};
    let writes = 0;
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => durableValue),
      write: vi.fn(async (record) => {
        writes += 1;
        if (writes === 1) {
          await new Promise<void>((resolve) => {
            releaseFirstWrite = resolve;
          });
        }
        durableValue = clone(record);
      }),
      clear: vi.fn(async () => {
        durableValue = null;
      }),
    };
    const controller = createLongformPersistence({
      localStorage: createLocalStorage().storage,
      durableStore,
      currentVersion: 9,
    });

    controller.storage.setItem('omnivoice.app', legacyEnvelope('first edit'));
    const draining = controller.flushPendingWrites();
    await vi.waitFor(() => expect(durableStore.write).toHaveBeenCalledOnce());

    controller.storage.setItem('omnivoice.app', legacyEnvelope('latest edit'));
    releaseFirstWrite();
    await draining;

    expect(durableStore.write).toHaveBeenCalledTimes(2);
    expect((durableValue as DurableLongformRecord | null)?.payload.script).toBe('latest edit');
  });

  it('keeps the latest exit fallback ahead of every queued durable revision', async () => {
    let durableValue: DurableLongformRecord | null = null;
    const releases: Array<() => void> = [];
    let writes = 0;
    const durableStore: LongformDurableStore = {
      read: vi.fn(async () => durableValue),
      write: vi.fn(async (record) => {
        writes += 1;
        if (writes <= 3) {
          const index = writes - 1;
          await new Promise<void>((resolve) => {
            releases[index] = resolve;
          });
        }
        durableValue = clone(record);
      }),
      clear: vi.fn(async () => {
        durableValue = null;
      }),
    };
    const local = createLocalStorage();
    const controller = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
    });

    controller.storage.setItem('omnivoice.app', legacyEnvelope('first edit'));
    const firstFlush = controller.flushPendingWrites();
    await vi.waitFor(() => expect(durableStore.write).toHaveBeenCalledOnce());

    controller.storage.setItem('omnivoice.app', legacyEnvelope('second edit'));
    const secondFlush = controller.flushPendingWrites();
    controller.storage.setItem('omnivoice.app', legacyEnvelope('latest edit'));
    const exitFlush = controller.flushPendingWritesForExit();
    expect(local.read()?.state.script).toBe('latest edit');

    releases[0]();
    await vi.waitFor(() => expect(durableStore.write).toHaveBeenCalledTimes(2));
    releases[1]();
    await vi.waitFor(() => expect(durableStore.write).toHaveBeenCalledTimes(3));
    expect(local.read()?.state.script).toBe('latest edit');
    expect((durableValue as DurableLongformRecord | null)?.payload.script).toBe('second edit');

    const timedOutReload = createLongformPersistence({
      localStorage: local.storage,
      durableStore,
      currentVersion: 9,
      getRole: () => 'readonly',
    });
    const restored = await timedOutReload.storage.getItem('omnivoice.app');
    expect(restored?.state.script).toBe('latest edit');

    releases[2]();
    await Promise.all([firstFlush, secondFlush, exitFlush]);
    expect((durableValue as DurableLongformRecord | null)?.payload.script).toBe('latest edit');
    expect(local.read()?.state).not.toHaveProperty('script');
  });

  it('still commits IndexedDB when the orderly-exit fallback is out of quota', async () => {
    const durable = createDurableStore();
    const controller = createLongformPersistence({
      localStorage: createLocalStorage(null, true).storage,
      durableStore: durable.store,
      currentVersion: 9,
      warn: () => {},
    });

    controller.storage.setItem('omnivoice.app', legacyEnvelope('durable exit copy'));

    await expect(controller.flushPendingWritesForExit()).resolves.toBeUndefined();
    expect(durable.read()?.payload.script).toBe('durable exit copy');
  });
});
