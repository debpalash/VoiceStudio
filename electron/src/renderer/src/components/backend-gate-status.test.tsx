import { Profiler } from 'react';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const fixture = vi.hoisted(() => {
  const listeners = new Set<(status: unknown) => void>();
  const status = {
    stage: 'installing',
    baseUrl: 'http://127.0.0.1:3900',
    port: 3900,
    managed: false,
    remote: false,
    elapsedMs: 1000,
    logTail: [],
    setupPhase: 'installing_deps',
    runtimePath: '/owned-test/runtime',
  };
  Object.defineProperty(window, 'voicestudio', {
    configurable: true,
    value: {
      app: { platform: 'win32', version: '0.0.0-test', isDev: false },
      backend: {
        getStatus: async () => status,
        onStatus: (listener: (status: unknown) => void) => {
          listeners.add(listener);
          return () => listeners.delete(listener);
        },
      },
      diagnostics: {
        getStatus: async () => ({
          enabled: false,
          opted_in: false,
          available: false,
          prompted: false,
        }),
      },
    },
  });
  return { listeners, status };
});
import '@/i18n';
import { BackendGate } from './backend-gate';
import { getBackendStatusSnapshot } from '@/hooks/use-backend-status';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const send = (status: object) => {
  for (const listener of fixture.listeners) listener(status);
};

it('renders each native elapsed update without a second passive counter commit', async () => {
  const frames: string[] = [];
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Profiler
        id="gate"
        onRender={() => frames.push(screen.queryByText(/^\d+s elapsed$/)?.textContent ?? '')}
      >
        <BackendGate>
          <div>workspace</div>
        </BackendGate>
      </Profiler>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(getBackendStatusSnapshot().stage).toBe('installing'));
  await act(async () => {
    await Promise.resolve();
  });
  frames.length = 0;
  await act(async () => {
    send({ ...fixture.status, elapsedMs: 2500 });
  });
  expect(frames).toEqual([expect.stringContaining('2s elapsed')]);
  expect(screen.getByText('2s elapsed')).toBeInTheDocument();
});

it('keeps elapsed ticks, status resync, unchanged floors, idle and a fresh start accurate', async () => {
  const clock = vi.spyOn(Date, 'now').mockReturnValue(100_000);
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  let commits = 0;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Profiler
        id="gate"
        onRender={() => {
          commits += 1;
        }}
      >
        <BackendGate>
          <div>workspace</div>
        </BackendGate>
      </Profiler>
    </QueryClientProvider>,
  );
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getByText('1s elapsed')).toBeInTheDocument();
  const resumeVisible = () => {
    visibility.mockReturnValue('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
    visibility.mockReturnValue('visible');
    document.dispatchEvent(new Event('visibilitychange'));
  };
  clock.mockReturnValue(102_000);
  act(resumeVisible);
  expect(screen.getByText('3s elapsed')).toBeInTheDocument();
  await act(async () => {
    send({ ...fixture.status, elapsedMs: 4999 });
  });
  expect(screen.getByText('4s elapsed')).toBeInTheDocument();
  commits = 0;
  await act(async () => {
    send({ ...fixture.status, elapsedMs: 4001, logTail: ['next line'] });
  });
  expect(screen.getByText('4s elapsed')).toBeInTheDocument();
  expect(commits).toBe(1);
  await act(async () => {
    send({ ...fixture.status, elapsedMs: 24_000 });
  });
  expect(screen.getByText('24s elapsed')).toBeInTheDocument();
  await act(async () => {
    send({ ...fixture.status, stage: 'failed', elapsedMs: 24_000 });
  });
  expect(screen.queryByText(/^\d+s elapsed$/)).toBeNull();
  commits = 0;
  clock.mockReturnValue(104_000);
  act(resumeVisible);
  expect(commits).toBe(0);
  await act(async () => {
    send({ ...fixture.status, elapsedMs: 500 });
  });
  expect(screen.getByText('0s elapsed')).toBeInTheDocument();
  clock.mockReturnValue(105_500);
  act(resumeVisible);
  expect(screen.getByText('2s elapsed')).toBeInTheDocument();
});
