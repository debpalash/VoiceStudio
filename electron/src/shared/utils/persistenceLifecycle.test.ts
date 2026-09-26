import { describe, expect, it, vi } from 'vitest';

import {
  afterApplicationPersistence,
  DESKTOP_PERSISTENCE_FLUSH_EVENT,
  flushApplicationPersistence,
  installDesktopPersistenceExitHandshake,
  PERSISTENCE_FLUSH_TIMEOUT_ERROR,
} from './persistenceLifecycle';

const cleanSummary = () => ({
  attempted: 1,
  written: 1,
  skipped: 0,
  failed: 0,
  discarded: 0,
});

describe('application persistence lifecycle', () => {
  it('awaits the document commit, then drains local state before navigation', async () => {
    let finishLongform!: () => void;
    const sequence: string[] = [];
    const flushLongform = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishLongform = () => {
            sequence.push('longform');
            resolve();
          };
        }),
    );
    const flushLocal = vi.fn(() => {
      sequence.push('local');
      return cleanSummary();
    });
    const navigate = vi.fn(() => sequence.push('navigate'));

    const pending = afterApplicationPersistence(navigate, { flushLongform, flushLocal });
    await Promise.resolve();
    expect(flushLocal).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();

    finishLongform();
    await pending;

    expect(sequence).toEqual(['longform', 'local', 'navigate']);
  });

  it('warns when the final synchronous fallback cannot be written', async () => {
    const warn = vi.fn();
    const summary = { ...cleanSummary(), written: 0, failed: 1 };

    await expect(
      flushApplicationPersistence({
        flushLongform: vi.fn(async () => {}),
        flushLocal: vi.fn(() => summary),
        warn,
      }),
    ).resolves.toBe(summary);

    expect(warn).toHaveBeenCalledWith('[persistence] 1 local write(s) could not be flushed');
  });

  it('runs an explicit recovery action when the pre-action flush rejects', async () => {
    const action = vi.fn(() => 'recovered');
    const warn = vi.fn();
    const error = new DOMException('blocked', 'SecurityError');

    await expect(
      afterApplicationPersistence(action, {
        flushLongform: vi.fn(async () => {
          throw error;
        }),
        flushLocal: vi.fn(cleanSummary),
        warn,
      }),
    ).resolves.toBe('recovered');

    expect(action).toHaveBeenCalledOnce();
    expect(warn).toHaveBeenCalledWith('[persistence] pre-action flush failed', error);
  });

  it('runs an updater/recovery action when IndexedDB never settles', async () => {
    vi.useFakeTimers();
    const sequence: string[] = [];
    const action = vi.fn(() => sequence.push('action'));
    const warn = vi.fn();
    const pending = afterApplicationPersistence(action, {
      flushLongform: vi.fn(() => new Promise<void>(() => {})),
      flushLocal: vi.fn(() => {
        sequence.push('local');
        return cleanSummary();
      }),
      timeoutMs: 25,
      warn,
    });

    await vi.advanceTimersByTimeAsync(24);
    expect(action).not.toHaveBeenCalled();
    expect(sequence).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    await pending;

    expect(sequence).toEqual(['local', 'action']);
    expect(warn).toHaveBeenCalledWith(
      '[persistence] pre-action flush failed',
      expect.objectContaining({ name: PERSISTENCE_FLUSH_TIMEOUT_ERROR }),
    );
    vi.useRealTimers();
  });

  it('confirms native exit only after the requested async flush settles', async () => {
    let onFlushRequest: (() => void) | undefined;
    let finishLongform!: () => void;
    const listen = vi.fn(async (event: string, handler: () => void) => {
      expect(event).toBe(DESKTOP_PERSISTENCE_FLUSH_EVENT);
      onFlushRequest = handler;
      return () => {};
    });
    const confirm = vi.fn(async () => {});
    const flushLocal = vi.fn(cleanSummary);

    await installDesktopPersistenceExitHandshake({
      desktop: true,
      listen,
      confirm,
      flushLongform: () =>
        new Promise<void>((resolve) => {
          finishLongform = resolve;
        }),
      flushLocal,
    });
    onFlushRequest?.();
    onFlushRequest?.();
    await Promise.resolve();

    expect(flushLocal).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();

    finishLongform();
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    expect(flushLocal).toHaveBeenCalledOnce();
  });

  it('acknowledges an explicit exit even when persistence reports a failure', async () => {
    let onFlushRequest: (() => void) | undefined;
    const confirm = vi.fn(async () => {});
    const warn = vi.fn();
    const flushLocal = vi.fn(cleanSummary);

    await installDesktopPersistenceExitHandshake({
      desktop: true,
      listen: async (_event, handler) => {
        onFlushRequest = handler;
        return () => {};
      },
      confirm,
      flushLongform: vi.fn(async () => {
        throw new DOMException('blocked', 'UnknownError');
      }),
      flushLocal,
      warn,
    });
    onFlushRequest?.();

    await vi.waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    expect(flushLocal).toHaveBeenCalledOnce();
    expect(warn).toHaveBeenCalledWith(
      '[persistence] orderly exit flush failed',
      expect.any(DOMException),
    );
  });

  it('does not install a native listener in browser builds', async () => {
    const listen = vi.fn();
    const cleanup = await installDesktopPersistenceExitHandshake({ desktop: false, listen });

    expect(listen).not.toHaveBeenCalled();
    expect(cleanup()).toBeUndefined();
  });

  it('keeps startup usable when the native event bridge cannot register', async () => {
    const warn = vi.fn();
    const cleanup = await installDesktopPersistenceExitHandshake({
      desktop: true,
      listen: vi.fn(async () => {
        throw new DOMException('bridge unavailable', 'InvalidStateError');
      }),
      warn,
    });

    expect(cleanup()).toBeUndefined();
    expect(warn).toHaveBeenCalledWith(
      '[persistence] native exit listener could not register',
      expect.any(DOMException),
    );
  });
});
