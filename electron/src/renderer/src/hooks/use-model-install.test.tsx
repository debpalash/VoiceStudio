import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { useModelInstall } from './use-model-install';
import { apiJson } from '@/lib/api/client';

const jobs = vi.hoisted(() => ({ data: undefined as unknown }));
vi.mock('@/hooks/use-model-install-sync', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/use-model-install-sync')>()),
  useModelInstallJobs: () => ({ data: jobs.data }),
}));
vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
  describeError: (error: unknown) => (error instanceof Error ? error.message : String(error)),
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

it('reads queued, downloading and ready in order and reports ready once', async () => {
  const onReady = vi.fn();
  const { result, rerender } = renderHook(() => useModelInstall('a/b', { onReady }), { wrapper });
  expect(result.current.view).toBeNull();
  await act(async () => void (await result.current.start()));
  expect(result.current.view?.stage).toBe('queued');
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'downloading', bytes_done: 2, total_bytes: 4 }] };
  rerender();
  expect(result.current.view).toMatchObject({ stage: 'downloading', pct: 50 });
  jobs.data = { jobs: [] };
  rerender();
  await waitFor(() => expect(result.current.view?.stage).toBe('ready'));
  rerender();
  expect(onReady).toHaveBeenCalledOnce();
});

it('shows cancelling, then cancelled with no ready or failure callback', async () => {
  const onReady = vi.fn();
  const onFailed = vi.fn();
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'downloading', bytes_done: 1, total_bytes: 4 }] };
  const { result, rerender } = renderHook(() => useModelInstall('a/b', { onReady, onFailed }), {
    wrapper,
  });
  // An install already running elsewhere is joined, never submitted twice.
  await act(async () => expect(await result.current.start()).toBe('joined'));
  expect(api).not.toHaveBeenCalledWith('/models/install', expect.anything());
  await act(async () => result.current.cancel());
  expect(api).toHaveBeenCalledWith(
    '/models/install/cancel',
    expect.objectContaining({ body: JSON.stringify({ repo_id: 'a/b' }) }),
  );
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'cancelling', bytes_done: 1, total_bytes: 4 }] };
  rerender();
  expect(result.current.view?.stage).toBe('cancelling');
  jobs.data = { jobs: [] };
  rerender();
  await waitFor(() => expect(result.current.view?.stage).toBe('cancelled'));
  expect(onReady).not.toHaveBeenCalled();
  expect(onFailed).not.toHaveBeenCalled();
});

it('keeps a classified failure with its next step until dismissed', async () => {
  const onFailed = vi.fn();
  const { result, rerender } = renderHook(() => useModelInstall('a/b', { onFailed }), { wrapper });
  await act(async () => void (await result.current.start()));
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'downloading', bytes_done: 1, total_bytes: 4 }] };
  rerender();
  jobs.data = {
    jobs: [
      {
        repo_id: 'a/b',
        state: 'failed',
        failed_at: 10,
        docs_topic: 'DISK_SPACE_LOW',
        error: 'No space left on device',
      },
    ],
  };
  rerender();
  await waitFor(() => expect(onFailed).toHaveBeenCalledOnce());
  expect(result.current.view).toMatchObject({ stage: 'failed', problem: 'disk' });
  act(() => result.current.dismiss());
  expect(result.current.view).toBeNull();
});

it('does not end a retry on the failure it replaced', async () => {
  const onFailed = vi.fn();
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'failed', failed_at: 10, error: 'offline' }] };
  const { result, rerender } = renderHook(() => useModelInstall('a/b', { onFailed }), { wrapper });
  expect(result.current.view).toMatchObject({ stage: 'failed', reason: 'offline' });
  await act(async () => void (await result.current.retry()));
  rerender();
  expect(onFailed).not.toHaveBeenCalled();
  expect(result.current.view?.stage).toBe('queued');
  jobs.data = {
    jobs: [{ repo_id: 'a/b', state: 'failed', failed_at: 20, error: 'still offline' }],
  };
  rerender();
  await waitFor(() => expect(onFailed).toHaveBeenCalledOnce());
  expect(result.current.view?.reason).toBe('still offline');
});

it('turns a refused install request into a failed view with the reason', async () => {
  api.mockRejectedValueOnce(new Error('Not enough disk space'));
  const { result } = renderHook(() => useModelInstall(undefined), { wrapper });
  await act(async () => expect(await result.current.start('c/d')).toBeNull());
  expect(result.current.repoId).toBe('c/d');
  expect(result.current.view).toMatchObject({ stage: 'failed', reason: 'Not enough disk space' });
});

it('follows a download started elsewhere and reports it ready when it lands', async () => {
  const onReady = vi.fn();
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'downloading', bytes_done: 1, total_bytes: 4 }] };
  const { result, rerender } = renderHook(() => useModelInstall('a/b', { onReady }), { wrapper });
  await waitFor(() => expect(result.current.active).toBe(true));
  api.mockImplementation(async (path: string) =>
    path === '/models' ? { models: [{ repo_id: 'a/b', installed: true }] } : {},
  );
  jobs.data = { jobs: [] };
  rerender();
  await waitFor(() => expect(onReady).toHaveBeenCalledWith('a/b'));
  expect(api).not.toHaveBeenCalledWith('/models/install', expect.anything());
});

it('reads a download cancelled elsewhere as cancelled, never ready', async () => {
  const onReady = vi.fn();
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'downloading', bytes_done: 1, total_bytes: 4 }] };
  const { result, rerender } = renderHook(() => useModelInstall('a/b', { onReady }), { wrapper });
  await waitFor(() => expect(result.current.active).toBe(true));
  // Cancelled between two polls: the job is simply gone and the catalogue
  // still lists the model as not installed.
  api.mockImplementation(async (path: string) =>
    path === '/models' ? { models: [{ repo_id: 'a/b', installed: false }] } : {},
  );
  jobs.data = { jobs: [] };
  rerender();
  await waitFor(() => expect(result.current.view?.stage).toBe('cancelled'));
  expect(onReady).not.toHaveBeenCalled();
});

it('treats a cancelling job seen from another control as a cancel', async () => {
  const onReady = vi.fn();
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'downloading', bytes_done: 1, total_bytes: 4 }] };
  const { result, rerender } = renderHook(() => useModelInstall('a/b', { onReady }), { wrapper });
  await waitFor(() => expect(result.current.active).toBe(true));
  jobs.data = { jobs: [{ repo_id: 'a/b', state: 'cancelling', bytes_done: 1, total_bytes: 4 }] };
  rerender();
  jobs.data = { jobs: [] };
  rerender();
  await waitFor(() => expect(result.current.view?.stage).toBe('cancelled'));
  expect(onReady).not.toHaveBeenCalled();
  expect(api).not.toHaveBeenCalledWith('/models', expect.anything());
});
