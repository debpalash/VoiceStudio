import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createCoalescedJsonStorage } from './coalescedJsonStorage';

function createMemoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      data.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      data.delete(key);
    }),
  };
}

class TestEventTarget {
  readonly listeners = new Map<string, Set<EventListener>>();
  readonly addEventListener = vi.fn((type: string, listener: EventListener) => {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  });
  readonly removeEventListener = vi.fn((type: string, listener: EventListener) => {
    this.listeners.get(type)?.delete(listener);
  });

  dispatch(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) listener(new Event(type));
  }
}

class TestVisibilityTarget extends TestEventTarget {
  visibilityState: DocumentVisibilityState = 'visible';
}

describe('coalesced JSON storage', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('defers providers and serialization, then coalesces a burst to its latest value', () => {
    const storage = createMemoryStorage();
    const stringify = vi.fn(JSON.stringify);
    const controller = createCoalescedJsonStorage({
      getStorage: () => storage,
      stringify,
    });
    controller.configurePersistenceRole('main');
    let latest = 0;
    const provider = vi.fn(() => ({ state: { count: latest }, version: 7 }));

    for (let count = 1; count <= 100; count += 1) {
      latest = count;
      controller.queueJsonWrite('omnivoice.app', provider);
    }

    expect(provider).not.toHaveBeenCalled();
    expect(stringify).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
    vi.advanceTimersByTime(249);
    expect(storage.setItem).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);

    expect(provider).toHaveBeenCalledTimes(1);
    expect(stringify).toHaveBeenCalledTimes(1);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    expect(JSON.parse(storage.data.get('omnivoice.app')!)).toEqual({
      state: { count: 100 },
      version: 7,
    });
  });

  it('reads a lazy provider at flush time instead of cloning its queued value', () => {
    const storage = createMemoryStorage();
    const stringify = vi.fn(JSON.stringify);
    const controller = createCoalescedJsonStorage({
      getStorage: () => storage,
      stringify,
    });
    controller.configurePersistenceRole('main');
    const source = { text: 'queued' };

    controller.queueJsonWrite('omni_ui', () => source);
    source.text = 'current';

    expect(stringify).not.toHaveBeenCalled();
    controller.flushPendingWrites();
    expect(storage.data.get('omni_ui')).toBe('{"text":"current"}');
  });

  it('binds a disposer to its own generation only', () => {
    const storage = createMemoryStorage();
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');

    const disposeOld = controller.queueJsonWrite('key', () => ({ value: 'old' }));
    const disposeNew = controller.queueJsonWrite('key', () => ({ value: 'new' }));
    disposeOld();
    vi.advanceTimersByTime(250);

    expect(storage.data.get('key')).toBe('{"value":"new"}');
    disposeNew();
    disposeNew();
  });

  it('lets the current generation disposer cancel its timers and write', () => {
    const storage = createMemoryStorage();
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');

    const dispose = controller.queueJsonWrite('key', () => ({ value: 1 }));
    dispose();
    vi.advanceTimersByTime(2_000);

    expect(storage.setItem).not.toHaveBeenCalled();
    expect(controller.flushPendingWrites().attempted).toBe(0);
  });

  it('preserves the hard deadline across replacements and starts a new window after flush', () => {
    const storage = createMemoryStorage();
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');
    let latest = 0;

    controller.queueJsonWrite('key', () => ({ latest }));
    for (let step = 1; step <= 4; step += 1) {
      vi.advanceTimersByTime(200);
      latest = step;
      // Replacing a provider is not cancellation. Consumers must not invoke
      // the generation disposer merely because a dependency changed, or that
      // genuine cancellation would correctly start a new durability window.
      controller.queueJsonWrite('key', () => ({ latest }));
    }
    vi.advanceTimersByTime(199);
    expect(storage.setItem).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    expect(storage.data.get('key')).toBe('{"latest":4}');

    latest = 5;
    controller.queueJsonWrite('key', () => ({ latest }));
    vi.advanceTimersByTime(249);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(storage.setItem).toHaveBeenCalledTimes(2);
    expect(storage.data.get('key')).toBe('{"latest":5}');
  });

  it('skips a physical write when the durable JSON is identical', () => {
    const storage = createMemoryStorage({ key: '{"value":1}' });
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');
    controller.queueJsonWrite('key', () => ({ value: 1 }));

    const result = controller.flushPendingWrites();

    expect(result).toEqual({ attempted: 1, written: 0, skipped: 1, failed: 0, discarded: 0 });
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(controller.flushPendingWrites().attempted).toBe(0);
  });

  it('returns a pending Zustand value synchronously before it is durable', () => {
    const storage = createMemoryStorage();
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');
    const adapter = controller.createZustandJsonStorage<{ count: number }>();
    const value = { state: { count: 8 }, version: 7 };

    adapter.setItem('omnivoice.app', value);

    expect(adapter.getItem('omnivoice.app')).toBe(value);
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it('parses durable Zustand data synchronously and safely falls back on bad reads', () => {
    const storage = createMemoryStorage({
      valid: '{"state":{"count":3},"version":7}',
      malformed: '{oops',
    });
    const warnings: string[] = [];
    const controller = createCoalescedJsonStorage({
      getStorage: () => storage,
      warn: (warning) => warnings.push(warning),
    });
    const adapter = controller.createZustandJsonStorage<{ count: number }>();

    expect(adapter.getItem('valid')).toEqual({ state: { count: 3 }, version: 7 });
    expect(adapter.getItem('malformed')).toBeNull();
    storage.getItem.mockImplementationOnce(() => {
      throw new DOMException('private content', 'SecurityError');
    });
    expect(adapter.getItem('unavailable')).toBeNull();
    expect(warnings).toEqual([
      '[persistence] parse failed for malformed (SyntaxError)',
      '[persistence] read failed for unavailable (SecurityError)',
    ]);

    controller.configurePersistenceRole('main');
    adapter.setItem('unavailable', { state: { count: 9 }, version: 7 });
    controller.flushPendingWrites();
    expect(storage.data.get('unavailable')).toBe('{"state":{"count":9},"version":7}');
  });

  it('offers a truthful physical read for destructive reset invariants', () => {
    const storage = createMemoryStorage({ malformed: '{oops' });
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });

    expect(() => controller.readDurableJsonValue('malformed')).toThrow(SyntaxError);
    storage.getItem.mockImplementationOnce(() => {
      throw new DOMException('private content', 'SecurityError');
    });
    expect(() => controller.readDurableJsonValue('unavailable')).toThrowError(DOMException);
  });

  it('cancels pending work before adapter removal and propagates main-window removal errors', () => {
    const storage = createMemoryStorage({ key: '{"old":true}' });
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');
    const adapter = controller.createZustandJsonStorage<unknown>();
    adapter.setItem('key', { state: { fresh: true } });

    adapter.removeItem('key');
    vi.advanceTimersByTime(2_000);

    expect(storage.data.has('key')).toBe(false);
    expect(storage.setItem).not.toHaveBeenCalled();
    storage.removeItem.mockImplementationOnce(() => {
      throw new DOMException('private content', 'SecurityError');
    });
    expect(() => adapter.removeItem('key')).toThrowError(DOMException);
  });

  it('discards selected keys and suspends matching future queues until resumed', () => {
    const storage = createMemoryStorage();
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');
    controller.queueJsonWrite('pref.one', () => 1);
    controller.queueJsonWrite('data.one', () => 2);

    const resume = controller.suspendJsonWrites((key) => key.startsWith('pref.'));
    controller.queueJsonWrite('pref.two', () => 3);
    vi.advanceTimersByTime(250);

    expect(storage.data.has('pref.one')).toBe(false);
    expect(storage.data.has('pref.two')).toBe(false);
    expect(storage.data.get('data.one')).toBe('2');

    resume();
    resume();
    controller.queueJsonWrite('pref.two', () => 4);
    vi.advanceTimersByTime(250);
    expect(storage.data.get('pref.two')).toBe('4');
  });

  it('discards throwing providers and serializers without poisoning later valid work', () => {
    const storage = createMemoryStorage();
    const warnings: string[] = [];
    const stringify = vi
      .fn<(value: unknown) => string | undefined>()
      .mockImplementationOnce(() => {
        throw new TypeError('sensitive serializer payload');
      })
      .mockImplementation(JSON.stringify);
    const controller = createCoalescedJsonStorage({
      getStorage: () => storage,
      stringify,
      warn: (warning) => warnings.push(warning),
    });
    controller.configurePersistenceRole('main');
    controller.queueJsonWrite('provider', () => {
      throw new RangeError('sensitive provider payload');
    });
    controller.queueJsonWrite('serializer', () => ({ secret: 'never log me' }));

    const failed = controller.flushPendingWrites();

    expect(failed).toMatchObject({ attempted: 2, failed: 2, discarded: 2 });
    expect(storage.setItem).not.toHaveBeenCalled();
    controller.queueJsonWrite('provider', () => ({ ok: 1 }));
    controller.queueJsonWrite('serializer', () => ({ ok: 2 }));
    controller.flushPendingWrites();
    expect(storage.data.get('provider')).toBe('{"ok":1}');
    expect(storage.data.get('serializer')).toBe('{"ok":2}');
    expect(warnings.join(' ')).not.toContain('sensitive');
    expect(warnings.join(' ')).not.toContain('never log me');
  });

  it('keeps a failed write dirty but disarms automatic retries until later activity', () => {
    const storage = createMemoryStorage({ key: '{"old":true}' });
    storage.setItem.mockImplementationOnce(() => {
      throw new DOMException('sensitive value', 'QuotaExceededError');
    });
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');
    let latest = 1;
    controller.queueJsonWrite('key', () => ({ latest }));

    vi.advanceTimersByTime(250);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    expect(storage.data.get('key')).toBe('{"old":true}');
    vi.advanceTimersByTime(5_000);
    expect(storage.setItem).toHaveBeenCalledTimes(1);

    latest = 2;
    controller.queueJsonWrite('key', () => ({ latest }));
    vi.advanceTimersByTime(250);
    expect(storage.setItem).toHaveBeenCalledTimes(2);
    expect(storage.data.get('key')).toBe('{"latest":2}');
  });

  it('isolates a failed key and retries it without rewriting successful siblings', () => {
    const storage = createMemoryStorage();
    storage.setItem.mockImplementation((key: string, value: string) => {
      if (key === 'bad') throw new DOMException('private', 'QuotaExceededError');
      storage.data.set(key, value);
    });
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    controller.configurePersistenceRole('main');
    controller.queueJsonWrite('good', () => ({ value: 1 }));
    controller.queueJsonWrite('bad', () => ({ value: 2 }));

    expect(controller.flushPendingWrites()).toMatchObject({ attempted: 2, written: 1, failed: 1 });
    expect(storage.data.get('good')).toBe('{"value":1}');
    storage.setItem.mockImplementation((key: string, value: string) => {
      storage.data.set(key, value);
    });

    expect(controller.flushPendingWrites()).toMatchObject({ attempted: 1, written: 1 });
    expect(storage.setItem.mock.calls.filter(([key]) => key === 'good')).toHaveLength(1);
    expect(storage.data.get('bad')).toBe('{"value":2}');
  });

  it('deduplicates privacy-safe warnings by key, operation, and error class', () => {
    const storage = createMemoryStorage();
    const warnings: string[] = [];
    const controller = createCoalescedJsonStorage({
      getStorage: () => storage,
      warn: (warning) => warnings.push(warning),
    });
    controller.configurePersistenceRole('main');

    for (const secret of ['first user text', 'second user text']) {
      controller.queueJsonWrite('omni_ui', () => {
        throw new TypeError(secret);
      });
      controller.flushPendingWrites();
    }

    expect(warnings).toEqual(['[persistence] provide failed for omni_ui (TypeError)']);
    expect(warnings[0]).not.toContain('user text');
  });

  it('stages only the final operation per key until the main role is known', () => {
    const storage = createMemoryStorage({ removed: 'old' });
    const controller = createCoalescedJsonStorage({ getStorage: () => storage });
    const adapter = controller.createZustandJsonStorage<{ value: string }>();

    adapter.setItem('removed', { state: { value: 'staged' } });
    adapter.removeItem('removed');
    adapter.removeItem('written');
    adapter.setItem('written', { state: { value: 'final' }, version: 7 });
    vi.advanceTimersByTime(10_000);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();

    controller.configurePersistenceRole('main');
    expect(storage.removeItem).toHaveBeenCalledTimes(1);
    expect(storage.data.has('removed')).toBe(false);
    vi.advanceTimersByTime(249);
    expect(storage.setItem).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(JSON.parse(storage.data.get('written')!)).toEqual({
      state: { value: 'final' },
      version: 7,
    });
  });

  it('keeps unknown and readonly roles free of serialization and raw mutation', () => {
    const storage = createMemoryStorage({ durable: '{"state":{"value":1}}' });
    const stringify = vi.fn(JSON.stringify);
    const controller = createCoalescedJsonStorage({
      getStorage: () => storage,
      stringify,
    });
    const adapter = controller.createZustandJsonStorage<{ value: number }>();
    adapter.setItem('new', { state: { value: 2 } });
    adapter.removeItem('durable');

    expect(controller.flushPendingWrites().attempted).toBe(0);
    expect(stringify).not.toHaveBeenCalled();
    controller.configurePersistenceRole('readonly');
    vi.advanceTimersByTime(2_000);
    adapter.setItem('another', { state: { value: 3 } });
    adapter.removeItem('durable');

    expect(storage.data.get('durable')).toBe('{"state":{"value":1}}');
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(adapter.getItem('durable')).toEqual({ state: { value: 1 } });
  });

  it('flushes once across hidden visibility and pagehide and owns one listener pair', () => {
    const storage = createMemoryStorage();
    const pagehide = new TestEventTarget();
    const visibility = new TestVisibilityTarget();
    const stringify = vi.fn(JSON.stringify);
    const controller = createCoalescedJsonStorage({
      getStorage: () => storage,
      stringify,
      getPagehideTarget: () => pagehide,
      getVisibilityTarget: () => visibility,
    });
    controller.configurePersistenceRole('main');

    const cleanupA = controller.installPersistenceLifecycleFlush();
    const cleanupB = controller.installPersistenceLifecycleFlush();
    expect(cleanupB).toBe(cleanupA);
    expect(pagehide.addEventListener).toHaveBeenCalledTimes(1);
    expect(visibility.addEventListener).toHaveBeenCalledTimes(1);
    controller.queueJsonWrite('key', () => ({ value: 1 }));
    visibility.dispatch('visibilitychange');
    expect(storage.setItem).not.toHaveBeenCalled();
    visibility.visibilityState = 'hidden';
    visibility.dispatch('visibilitychange');
    pagehide.dispatch('pagehide');

    expect(stringify).toHaveBeenCalledTimes(1);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    cleanupA();
    cleanupB();
    expect(pagehide.removeEventListener).toHaveBeenCalledTimes(1);
    expect(visibility.removeEventListener).toHaveBeenCalledTimes(1);
  });

  it('fully resets isolated state, timers, suspensions, listeners, and warning dedupe', () => {
    const storage = createMemoryStorage();
    const pagehide = new TestEventTarget();
    const visibility = new TestVisibilityTarget();
    const warnings: string[] = [];
    const controller = createCoalescedJsonStorage({
      getStorage: () => storage,
      warn: (warning) => warnings.push(warning),
      getPagehideTarget: () => pagehide,
      getVisibilityTarget: () => visibility,
    });
    controller.configurePersistenceRole('main');
    controller.installPersistenceLifecycleFlush();
    controller.suspendJsonWrites((key) => key === 'blocked');
    controller.queueJsonWrite('bad', () => {
      throw new TypeError('private');
    });
    controller.flushPendingWrites();
    controller.queueJsonWrite('pending', () => ({ value: 1 }));

    controller.resetForTests();
    vi.advanceTimersByTime(2_000);
    expect(storage.data.has('pending')).toBe(false);
    expect(pagehide.removeEventListener).toHaveBeenCalledTimes(1);
    expect(visibility.removeEventListener).toHaveBeenCalledTimes(1);

    controller.queueJsonWrite('blocked', () => ({ value: 2 }));
    controller.queueJsonWrite('bad', () => {
      throw new TypeError('private again');
    });
    controller.configurePersistenceRole('main');
    controller.flushPendingWrites();
    expect(storage.data.get('blocked')).toBe('{"value":2}');
    expect(warnings).toHaveLength(2);
  });
});
