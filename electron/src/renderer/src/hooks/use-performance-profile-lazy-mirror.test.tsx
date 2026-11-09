import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { usePerformanceProfile, type PerformanceChoice } from './use-performance-profile';

const mocks = vi.hoisted(() => {
  let release!: () => void;
  const ready = new Promise<void>((done) => {
    release = done;
  });
  return { ready, release: () => release(), clone: vi.fn(), api: vi.fn() };
});
vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api }));
vi.mock('./use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
vi.mock('@/lib/store/clone-settings', async () => {
  await mocks.ready;
  return { patchCloneSettings: mocks.clone };
});

it('confirms saved presets while draft code loads and discards superseded mirrors across controls', async () => {
  const state = (global: PerformanceChoice, engine = 'omnivoice', steps = 32) => ({
    global,
    targets: { tts: { steps, postprocess: true } },
    selections: { tts: { engine } },
    tts_tiered_engines: ['omnivoice'],
  });
  mocks.api.mockResolvedValue(state('balanced'));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result, unmount } = renderHook(
    () => [usePerformanceProfile(), usePerformanceProfile()] as const,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    },
  );
  const saves: Promise<unknown>[] = [];
  const apply = async (
    control: 0 | 1,
    tier: PerformanceChoice,
    engine = 'omnivoice',
    steps = 32,
  ) => {
    mocks.api.mockResolvedValue(state(tier, engine, steps));
    await act(async () => {
      const save = result.current[control].setTier({ tier, family: null });
      saves.push(save);
      let settled = false;
      void save.then(
        () => {
          settled = true;
        },
        () => {
          settled = true;
        },
      );
      await vi.waitFor(() => expect(settled).toBe(true), { timeout: 300 });
      await save;
    });
    expect(result.current[control].isSaving).toBe(false);
    expect(client.getQueryData(['performance-profile'])).toEqual(state(tier, engine, steps));
  };
  try {
    await apply(0, 'quality');
    expect(mocks.clone).not.toHaveBeenCalled();
    await apply(1, 'fast', 'omnivoice', 8);
    await apply(0, 'auto', 'kittentts');
    mocks.release();
    await act(async () => {
      await vi.dynamicImportSettled();
    });
    expect(mocks.clone).not.toHaveBeenCalled();
    await apply(1, 'max', 'omnivoice', 64);
    await act(async () => {
      await vi.dynamicImportSettled();
    });
    expect(mocks.clone).toHaveBeenLastCalledWith({ steps: 64, postprocess: true });
  } finally {
    mocks.release();
    await act(async () => {
      await Promise.allSettled(saves);
      await vi.dynamicImportSettled();
    });
    unmount();
    client.clear();
  }
});
