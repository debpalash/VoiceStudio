import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock('@/lib/api/client', () => ({
  apiJson: mock.api,
  describeError: (error: unknown) => (error instanceof Error ? error.message : String(error)),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { AUDIOCPP_TTS_ENGINES, AudioCppRuntimeInstall } from './audiocpp-runtime-install';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderInstall() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <AudioCppRuntimeInstall />
    </QueryClientProvider>,
  );
}

it('covers both audio.cpp TTS engines (#2665)', () => {
  expect([...AUDIOCPP_TTS_ENGINES].sort()).toEqual(['audiocpp', 'audiocpp-dots']);
});

it('installs the shared runtime from a TTS engine row', async () => {
  let installed = false;
  mock.api.mockImplementation((path: string, init?: RequestInit) => {
    if (path === '/engines/audiocpp/runtime/install' && init?.method === 'POST') {
      installed = true;
      return Promise.resolve({});
    }
    if (path === '/engines/audiocpp/runtime/install/status')
      return Promise.resolve({ installed, supported: true, job: { state: 'idle', progress: 0 } });
    return Promise.resolve({});
  });
  renderInstall();
  fireEvent.click(await screen.findByRole('button', { name: 'modelMaintenance.install' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/engines/audiocpp/runtime/install', { method: 'POST' }),
  );
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: 'modelMaintenance.install' })).toBeNull(),
  );
});

it('offers nothing where the runtime is installed or has no prebuilt', async () => {
  mock.api.mockResolvedValue({ installed: false, supported: false });
  renderInstall();
  await waitFor(() => expect(mock.api).toHaveBeenCalled());
  expect(screen.queryByRole('button')).toBeNull();
});
