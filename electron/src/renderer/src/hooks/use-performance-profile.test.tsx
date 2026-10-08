import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { usePerformanceProfile } from './use-performance-profile';

const mocks = vi.hoisted(() => ({ dub: vi.fn(), clone: vi.fn(), api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api }));
vi.mock('@/lib/store/clone-settings', () => ({ patchCloneSettings: mocks.clone }));
vi.mock('@/features/dub/dub-session', () => ({ setDubProduction: mocks.dub }));
vi.mock('./use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));

it('changes the global preset without replacing Dubbing production overrides', async () => {
  const state = {
    targets: { tts: { steps: 32, postprocess: true } },
    selections: { tts: { engine: 'omnivoice' } },
    tts_tiered_engines: ['omnivoice', 'omnivoice-subprocess'],
  };
  mocks.api.mockResolvedValue(state);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result, unmount } = renderHook(() => usePerformanceProfile(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await act(async () => {
    await result.current.setTier({ tier: 'quality', family: null });
  });
  expect(mocks.clone).toHaveBeenCalledWith({ steps: 32, postprocess: true });
  expect(mocks.dub).not.toHaveBeenCalled();
  expect(mocks.api.mock.calls.filter(([, init]) => init?.method === 'PUT')).toEqual([[
    '/api/settings/performance-profile',
    {
    method: 'PUT',
    body: JSON.stringify({ tier: 'quality', family: null }),
    },
  ]]);
  unmount();
  client.clear();
});

it('keeps an applied backend preset successful when the clone draft chunk cannot load', async () => {
  mocks.clone.mockImplementationOnce(() => {
    throw new Error('draft unavailable');
  });
  mocks.api.mockResolvedValue({
    targets: { tts: { steps: 8, postprocess: false } },
    selections: { tts: { engine: 'omnivoice' } },
    tts_tiered_engines: ['omnivoice', 'omnivoice-subprocess'],
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result, unmount } = renderHook(() => usePerformanceProfile(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });

  await expect(act(async () => result.current.setTier({ tier: 'fast', family: 'tts' }))).resolves.toEqual(
    expect.objectContaining({ targets: { tts: { steps: 8, postprocess: false } } }),
  );
  expect(mocks.api).toHaveBeenCalledWith('/api/settings/performance-profile', {
    method: 'PUT',
    body: JSON.stringify({ tier: 'fast', family: 'tts' }),
  });
  unmount();
  client.clear();
});

it('finishes saving before dependent catalogue refreshes complete', async () => {
  mocks.api.mockResolvedValue({
    targets: { tts: { steps: 16, postprocess: true } },
    selections: { tts: { engine: 'omnivoice' } },
    tts_tiered_engines: ['omnivoice', 'omnivoice-subprocess'],
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.spyOn(client, 'invalidateQueries').mockImplementation(() => new Promise(() => {}));
  const { result, unmount } = renderHook(() => usePerformanceProfile(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });

  let saved = false;
  await act(async () => {
    void result.current.setTier({ tier: 'balanced', family: null }).then(() => {
      saved = true;
    });
    await vi.waitFor(() => expect(saved).toBe(true));
  });

  unmount();
  client.clear();
});

it('mirrors the preset into the clone draft only for backend-listed tiered engines', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result, unmount } = renderHook(() => usePerformanceProfile(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  const tiered = ['omnivoice', 'omnivoice-subprocess'];
  for (const [engine, mirrored] of [
    ['omnivoice-subprocess', true],
    ['kittentts', false],
  ] as const) {
    mocks.clone.mockClear();
    mocks.api.mockResolvedValue({
      targets: { tts: { steps: 8, postprocess: false } },
      selections: { tts: { engine } },
      tts_tiered_engines: tiered,
    });
    await act(async () => {
      await result.current.setTier({ tier: 'fast', family: null });
    });
    if (mirrored) expect(mocks.clone).toHaveBeenCalledWith({ steps: 8, postprocess: false });
    else expect(mocks.clone).not.toHaveBeenCalled();
  }
  unmount();
  client.clear();
});
