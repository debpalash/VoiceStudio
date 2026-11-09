import type { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CATALOGUE_POLL_WHILE_WAITING_MS } from '@/features/settings/model-catalogue-query';
import { useTtsReadiness } from './use-tts-readiness';

const disk = vi.hoisted(() => ({ installed: false, reads: 0 }));

vi.mock('./use-backend-status', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./use-backend-status')>();
  return { ...actual, useBackendStatus: () => ({ stage: 'ready' }) };
});

// The engine itself is available: only its model is missing on disk.
vi.mock('./use-engines', () => ({
  engineFamilyState: (data: Record<string, unknown> | undefined, family: string) => data?.[family],
  useEngines: () => ({
    data: {
      tts: {
        active: 'mlx-audio',
        active_model: 'org/voice-design',
        backends: [{ id: 'mlx-audio', available: true, supports_cloning: true }],
      },
    },
    activeTtsReady: true,
    isLoading: false,
    isError: false,
  }),
}));

vi.mock('@/lib/api/client', () => ({
  apiJson: vi.fn(async () => {
    disk.reads += 1;
    return { models: [{ repo_id: 'org/voice-design', installed: disk.installed }] };
  }),
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  disk.installed = false;
  disk.reads = 0;
});

afterEach(() => {
  vi.useRealTimers();
});

it('clears the "not ready" gate once a model finished downloading elsewhere', async () => {
  const { result } = renderHook(() => useTtsReadiness(), { wrapper });
  await act(() => vi.advanceTimersByTimeAsync(10));
  expect(result.current).toBe('engine');

  // Finished in Settings, another window or the CLI: nothing invalidates this screen.
  disk.installed = true;
  await act(() => vi.advanceTimersByTimeAsync(CATALOGUE_POLL_WHILE_WAITING_MS + 10));

  expect(result.current).toBeNull();
});

it('stops re-reading the catalogue once the model is on disk', async () => {
  disk.installed = true;
  const { result } = renderHook(() => useTtsReadiness(), { wrapper });
  await act(() => vi.advanceTimersByTimeAsync(10));
  expect(result.current).toBeNull();
  const reads = disk.reads;

  await act(() => vi.advanceTimersByTimeAsync(CATALOGUE_POLL_WHILE_WAITING_MS * 3));

  expect(disk.reads).toBe(reads);
});
