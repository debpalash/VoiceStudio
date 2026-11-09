import { afterEach, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';
import { QueryClient, onlineManager } from '@tanstack/react-query';
afterEach(() => {
  onlineManager.setEventListener((setOnline) => {
    const online = () => setOnline(true);
    const offline = () => setOnline(false);
    window.addEventListener('online', online);
    window.addEventListener('offline', offline);
    return () => {
      window.removeEventListener('online', online);
      window.removeEventListener('offline', offline);
    };
  });
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

async function nativeConnectivity() {
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
  const client = new QueryClient();
  client.mount();
  return {
    client,
    push: (stage: 'ready' | 'unresponsive' | 'crashed', remote = false) =>
      push({ ...status.FALLBACK_BACKEND_STATUS, stage, remote }),
    stop: () => {
      onlineManager.setOnline(true);
      client.unmount();
      client.clear();
      stop();
    },
  };
}

// Exercise actual mutation execution after browser connectivity changes.
it.each(['ready', 'unresponsive'] as const)(
  'runs local saves without internet while the native backend is %s',
  async (stage) => {
    const native = await nativeConnectivity();
    try {
      native.push(stage);
      window.dispatchEvent(new Event('offline'));
      const save = vi.fn().mockResolvedValue({ id: 'offline-profile' });
      const mutation = native.client.getMutationCache().build(native.client, {
        mutationFn: save,
        gcTime: 0,
      });
      const pending = mutation.execute({ name: 'Offline voice' });
      await waitFor(() => expect(save).toHaveBeenCalledOnce());
      await expect(pending).resolves.toEqual({ id: 'offline-profile' });
      expect(mutation.state.isPaused).toBe(false);
    } finally {
      native.stop();
    }
  },
);

it('internet reconnect cannot resume requests before native readiness or after a crash', async () => {
  const native = await nativeConnectivity();
  try {
    window.dispatchEvent(new Event('online'));
    expect(onlineManager.isOnline()).toBe(false);
    native.push('ready');
    expect(onlineManager.isOnline()).toBe(true);
    native.push('crashed');
    window.dispatchEvent(new Event('online'));
    expect(onlineManager.isOnline()).toBe(false);
  } finally {
    native.stop();
  }
});

it('keeps browser connectivity handling without a native supervisor', async () => {
  vi.stubGlobal('voicestudio', undefined);
  await import('./use-backend-status');
  const client = new QueryClient();
  client.mount();
  try {
    window.dispatchEvent(new Event('offline'));
    expect(onlineManager.isOnline()).toBe(false);
    window.dispatchEvent(new Event('online'));
    expect(onlineManager.isOnline()).toBe(true);
  } finally {
    client.unmount();
    client.clear();
  }
});

it.each(['ready', 'unresponsive'] as const)(
  'queues disconnected remote saves while the supervisor reports %s',
  async (stage) => {
    const native = await nativeConnectivity();
    try {
      native.push(stage, true);
      window.dispatchEvent(new Event('offline'));
      const save = vi.fn().mockResolvedValue({ id: 'remote-profile' });
      const mutation = native.client.getMutationCache().build(native.client, {
        mutationFn: save,
        gcTime: 0,
      });
      const pending = mutation.execute({ name: 'Remote voice' });
      await waitFor(() => expect(mutation.state.isPaused).toBe(true));
      expect(save).not.toHaveBeenCalled();
      native.push('ready', true);
      expect(onlineManager.isOnline()).toBe(false);
      native.push('crashed', true);
      window.dispatchEvent(new Event('online'));
      expect(onlineManager.isOnline()).toBe(false);
      expect(save).not.toHaveBeenCalled();
      native.push('ready', true);
      await expect(pending).resolves.toEqual({ id: 'remote-profile' });
      expect(save).toHaveBeenCalledOnce();
    } finally {
      native.stop();
    }
  },
);

it('keeps remote startup offline but allows switching to a local backend', async () => {
  vi.stubGlobal('navigator', { onLine: false });
  const native = await nativeConnectivity();
  try {
    native.push('ready', true);
    expect(onlineManager.isOnline()).toBe(false);
    native.push('ready');
    expect(onlineManager.isOnline()).toBe(true);
    native.push('ready', true);
    expect(onlineManager.isOnline()).toBe(false);
  } finally {
    native.stop();
  }
});
