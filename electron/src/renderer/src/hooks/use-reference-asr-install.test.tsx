import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { useReferenceAsrInstall } from './use-reference-asr-install';
import { apiJson } from '@/lib/api/client';

const jobs = vi.hoisted(() => ({ data: undefined as unknown }));
vi.mock('@/hooks/use-model-install-sync', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/use-model-install-sync')>()),
  useModelInstallJobs: () => ({ data: jobs.data }),
}));
vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
}));
const api = vi.mocked(apiJson);
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);
beforeEach(() => {
  api.mockReset();
  api.mockResolvedValue({});
  jobs.data = { jobs: [] };
});

it('downloads nothing until started, then reports completion once', async () => {
  const done = vi.fn();
  const { result, rerender } = renderHook(() => useReferenceAsrInstall('a/b', done), { wrapper });
  expect(api).not.toHaveBeenCalled();
  await act(async () => result.current.start());
  expect(api).toHaveBeenCalledWith(
    '/models/install',
    expect.objectContaining({ body: JSON.stringify({ repo_id: 'a/b' }) }),
  );
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'downloading', bytes_done: 1, total_bytes: 4 }] };
  rerender();
  await waitFor(() => expect(result.current.progress).toBe(25));
  expect(done).not.toHaveBeenCalled();
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'done' }] };
  rerender();
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
});

it('surfaces a failed install without retrying the transcript', async () => {
  const done = vi.fn();
  const { result, rerender } = renderHook(() => useReferenceAsrInstall('a/b', done), { wrapper });
  await act(async () => result.current.start());
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'failed', error: 'offline' }] };
  rerender();
  await waitFor(() => expect(result.current.error).toBe('offline'));
  expect(done).not.toHaveBeenCalled();
});
