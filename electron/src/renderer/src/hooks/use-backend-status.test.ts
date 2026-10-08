import { afterEach, expect, it, vi } from 'vitest';
import { onlineManager } from '@tanstack/react-query';
afterEach(() => {
  onlineManager.setOnline(true);
  vi.unstubAllGlobals();
  vi.resetModules();
});
it('does not advertise a native backend as ready before its first status', async () => {
  vi.stubGlobal('voicestudio', { backend: {} });
  const status = await import('./use-backend-status');
  expect(status.getBackendStatusSnapshot().stage).toBe('attaching');
});
it('does not replace a newer pushed status with an older initial snapshot', async () => {
  let push!: (status: unknown) => void;
  let finish!: (status: unknown) => void;
  vi.stubGlobal('voicestudio', {
    backend: {
      onStatus: (callback: typeof push) => {
        push = callback;
        return () => {};
      },
      getStatus: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    },
  });
  const status = await import('./use-backend-status');
  const stop = status.subscribeBackendStatus(() => {});
  const base = status.FALLBACK_BACKEND_STATUS;
  push({ ...base, stage: 'crashed' });
  finish({ ...base, stage: 'ready' });
  await Promise.resolve();
  expect(status.getBackendStatusSnapshot().stage).toBe('crashed');
  stop();
});

it('pauses backend queries until the native supervisor reports ready', async () => {
  onlineManager.setOnline(true);
  let push!: (status: unknown) => void;
  vi.stubGlobal('voicestudio', {
    backend: {
      onStatus: (callback: typeof push) => {
        push = callback;
        return () => {};
      },
      getStatus: () => new Promise(() => {}),
    },
  });

  const status = await import('./use-backend-status');
  expect(onlineManager.isOnline()).toBe(false);
  const stop = status.subscribeBackendStatus(() => {});
  push({ ...status.FALLBACK_BACKEND_STATUS, stage: 'ready' });
  expect(onlineManager.isOnline()).toBe(true);
  push({ ...status.FALLBACK_BACKEND_STATUS, stage: 'crashed' });
  expect(onlineManager.isOnline()).toBe(false);
  stop();
});

// #2430: `unresponsive` is a live-but-busy backend, not a dead one. The
// supervisor proves the child is alive before publishing it, so React Query
// must stay online — going offline here pauses every query and leaves the
// workspace visible but inert for the whole duration of a long generation.
it('stays online while a live backend is only busy, and offline when it dies', async () => {
  onlineManager.setOnline(true);
  let push!: (status: unknown) => void;
  vi.stubGlobal('voicestudio', {
    backend: {
      onStatus: (callback: typeof push) => {
        push = callback;
        return () => {};
      },
      getStatus: () => new Promise(() => {}),
    },
  });

  const status = await import('./use-backend-status');
  const stop = status.subscribeBackendStatus(() => {});
  const base = status.FALLBACK_BACKEND_STATUS;

  push({ ...base, stage: 'unresponsive' });
  expect(onlineManager.isOnline()).toBe(true);
  // ...and it recovers on its own once the job releases the event loop.
  push({ ...base, stage: 'ready' });
  expect(onlineManager.isOnline()).toBe(true);
  push({ ...base, stage: 'crashed' });
  expect(onlineManager.isOnline()).toBe(false);
  stop();
});

it('treats only ready and unresponsive as able to answer a request (#2430)', async () => {
  const { isBackendReachable } = await import('./use-backend-status');
  expect(isBackendReachable('ready')).toBe(true);
  expect(isBackendReachable('unresponsive')).toBe(true);
  // Everything else either is not listening yet or is terminally gone.
  for (const stage of ['crashed', 'failed', 'port_in_use', 'starting', 'attaching'] as const) {
    expect(isBackendReachable(stage)).toBe(false);
  }
});

// #2594: a busy backend answers every poll late. The supervisor stage must
// reach the poll-interval helpers so background polls back off during a stall.
it('lengthens background status polls while the backend is busy', async () => {
  let push!: (status: unknown) => void;
  vi.stubGlobal('voicestudio', {
    backend: {
      onStatus: (callback: typeof push) => {
        push = callback;
        return () => {};
      },
      getStatus: () => new Promise(() => {}),
    },
  });

  const status = await import('./use-backend-status');
  const polling = await import('@/lib/status-polling');
  const stop = status.subscribeBackendStatus(() => {});
  push({ ...status.FALLBACK_BACKEND_STATUS, stage: 'ready' });
  expect(polling.modelStatusPollMs(1, 'ready')).toBe(polling.ACTIVE_STATUS_POLL_MS);
  push({ ...status.FALLBACK_BACKEND_STATUS, stage: 'unresponsive' });
  expect(polling.modelStatusPollMs(1, 'ready')).toBe(polling.BUSY_BACKEND_POLL_MS);
  push({ ...status.FALLBACK_BACKEND_STATUS, stage: 'ready' });
  expect(polling.modelStatusPollMs(1, 'ready')).toBe(polling.ACTIVE_STATUS_POLL_MS);
  stop();
});
