import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@tanstack/react-router', () => ({ Link: (props: any) => <a>{props.children}</a> }));
import { DictationSetup } from './dictation-setup';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mount(onReady = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DictationSetup onReady={onReady} />
    </QueryClientProvider>,
  );
  return onReady;
}

it('selects an installed model inline without starting a download', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/dictation/models')
      return Promise.resolve({
        engine_available: true,
        models: [
          {
            id: 'installed',
            repo_id: 'owner/installed',
            label: 'Installed ASR',
            tag: 'offline',
            recommended: false,
            size_gb: 0.1,
            installed: true,
          },
        ],
      });
    return Promise.resolve({});
  });
  const onReady = mount();

  fireEvent.click(await screen.findByRole('button', { name: 'asr_missing.use' }));
  await waitFor(() => expect(onReady).toHaveBeenCalled());
  expect(mock.api).toHaveBeenCalledWith(
    '/dictation/prefs',
    expect.objectContaining({ body: JSON.stringify({ model_id: 'installed', enabled: true }) }),
  );
  expect(mock.api.mock.calls.some(([path]) => path === '/models/install')).toBe(false);
});

it('starts only the recommended model after an explicit click', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/dictation/models')
      return Promise.resolve({
        engine_available: true,
        models: [
          {
            id: 'recommended',
            repo_id: 'owner/recommended',
            label: 'Recommended ASR',
            tag: 'streaming',
            recommended: true,
            size_gb: 0.04,
            installed: false,
          },
        ],
      });
    // The backend lists a job as soon as it accepts the install request.
    if (path === '/models/install/status')
      return Promise.resolve({
        jobs: mock.api.mock.calls.some(([called]) => called === '/models/install')
          ? [{ repo_id: 'owner/recommended', state: 'downloading' }]
          : [],
      });
    return Promise.resolve({});
  });
  mount();

  fireEvent.click(await screen.findByRole('button', { name: 'asr_missing.download' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/models/install',
      expect.objectContaining({
        body: JSON.stringify({ repo_id: 'owner/recommended' }),
      }),
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole('status')).toHaveTextContent('Recommended ASR · modelInstall.queued'),
  );
  expect(screen.getByRole('button', { name: 'common.cancel' })).toBeInTheDocument();
});
