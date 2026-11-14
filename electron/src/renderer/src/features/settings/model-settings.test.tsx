import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

vi.mock('@/lib/api/client', () => ({
  apiJson: mock.api,
  describeError: (error: unknown) => (error instanceof Error ? error.message : String(error)),
  ApiError: class ApiError extends Error {},
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="#">{children}</a>,
}));
vi.mock('sonner', () => ({ toast: mock.toast }));

import { DiarisationSettings } from './model-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('waits for confirmed runtime support and recovers a failed status check', async () => {
  let runtimeChecks = 0;
  mock.api.mockImplementation((path: string) => {
    if (path === '/engines/diarisation')
      return Promise.resolve({
        active: 'pyannote',
        options: [
          {
            id: 'audiocpp-sortformer',
            label: 'Sortformer',
            model: 'audio-cpp/audio.cpp-gguf',
            model_installed: true,
            runtime_installed: false,
            installed: false,
          },
        ],
      });
    if (path === '/engines/audiocpp/runtime/install/status') {
      runtimeChecks += 1;
      if (runtimeChecks === 1) return Promise.reject(new Error('Runtime status unavailable'));
      return Promise.resolve({
        installed: false,
        supported: true,
        job: { state: 'idle', progress: 0 },
      });
    }
    return Promise.resolve({});
  });

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DiarisationSettings />
    </QueryClientProvider>,
  );

  await screen.findByText('Sortformer');
  const error = await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: 'modelMaintenance.install' })).toBeNull();
  expect(within(error).getByText('Runtime status unavailable')).toBeInTheDocument();

  fireEvent.click(within(error).getByRole('button', { name: 'backend.retry' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'modelMaintenance.install' })).toBeEnabled(),
  );
});

it('confirms a diarisation engine selection after the backend accepts it', async () => {
  mock.api.mockImplementation((path: string, init?: RequestInit) => {
    if (path === '/engines/diarisation/select' && init?.method === 'POST')
      return Promise.resolve({ active: 'audiocpp-sortformer' });
    if (path === '/engines/diarisation')
      return Promise.resolve({
        active: 'pyannote',
        options: [
          {
            id: 'audiocpp-sortformer',
            label: 'Sortformer',
            installed: true,
            model_installed: true,
            runtime_installed: true,
          },
        ],
      });
    if (path === '/engines/audiocpp/runtime/install/status')
      return Promise.resolve({
        installed: true,
        supported: true,
        job: { state: 'idle', progress: 1 },
      });
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <DiarisationSettings />
    </QueryClientProvider>,
  );

  fireEvent.click(await screen.findByRole('button', { name: 'modelSettings.select' }));
  await waitFor(() => expect(mock.toast.success).toHaveBeenCalledWith('settings.engine_switched'));
});

it('explains remote native installation restrictions before a POST', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/engines/diarisation'
        ? {
            active: 'pyannote',
            options: [
              {
                id: 'audiocpp-sortformer',
                label: 'Sortformer',
                model_installed: true,
                runtime_installed: false,
                installed: false,
              },
            ],
          }
        : {
            installed: false,
            supported: true,
            install_allowed: false,
            job: { state: 'idle', progress: 0 },
          },
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DiarisationSettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('engines.localInstallRequired')).toBeInTheDocument();
  const button = screen.getByRole('button', { name: 'modelMaintenance.install' });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(mock.api.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('shows the licence chip on a diarisation option whose model needs acceptance (#3642)', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/engines/diarisation')
      return Promise.resolve({
        active: 'audiocpp-sortformer',
        options: [
          {
            id: 'audiocpp-sortformer',
            label: 'Sortformer v1 (audio.cpp)',
            model: 'audio-cpp/audio.cpp-gguf',
            model_installed: true,
            runtime_installed: true,
            installed: true,
          },
        ],
      });
    if (path === '/models')
      return Promise.resolve({
        models: [
          {
            repo_id: 'audio-cpp/audio.cpp-gguf',
            label: 'audio.cpp native bundle',
            role: 'TTS',
            engines: ['audiocpp'],
            families: ['tts', 'diarisation'],
            installed: true,
            license_info: { license: 'other', license_category: 'noncommercial' },
            license_acceptance: {
              repo_id: 'audio-cpp/audio.cpp-gguf',
              required: true,
              accepted: false,
              state: 'not_accepted',
            },
          },
        ],
      });
    return Promise.resolve({
      installed: true,
      supported: true,
      job: { state: 'idle', progress: 0 },
    });
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DiarisationSettings />
    </QueryClientProvider>,
  );

  const row =
    (await screen.findByText('Sortformer v1 (audio.cpp)')).closest('[data-slot]') ?? document.body;
  expect(
    await within(row as HTMLElement).findByRole('button', { name: /^modelLicense\.label:/ }),
  ).toBeTruthy();
});

it.each(['model', 'dependency', 'incomplete-dependencies'] as const)(
  'opens the installed Sortformer licence action for %s without reinstalling or granting acceptance',
  async (pending) => {
    const repo = 'audio-cpp/audio.cpp-gguf';
    const dependency = 'fixture/required-tokenizer';
    const acceptance = (repoId: string, accepted: boolean) => ({
      repo_id: repoId,
      required: true,
      accepted,
      category: 'noncommercial',
      fingerprint: (repoId === repo ? 'a' : 'b').repeat(64),
    });
    const own = acceptance(repo, pending !== 'model');
    const requiredDependency = acceptance(dependency, false);
    const info = {
      license: 'CC-BY-NC-4.0',
      license_category: 'noncommercial',
      review_status: 'unreviewed',
      blockers: [],
      variants: [],
    };
    mock.api.mockImplementation((path: string) => {
      if (path === '/models')
        return Promise.resolve({
          models: [
            {
              repo_id: repo,
              label: 'audio.cpp native bundle',
              role: 'TTS',
              engines: ['audiocpp'],
              families: ['tts', 'diarisation'],
              installed: true,
              supported: true,
              size_gb: 4.98,
              license_info: info,
              license_acceptance: own,
              license_dependencies: pending === 'dependency' ? [requiredDependency] : [],
              license_dependencies_complete: pending !== 'incomplete-dependencies',
            },
          ],
        });
      if (path === '/engines/diarisation')
        return Promise.resolve({
          active: 'pyannote',
          options: [
            {
              id: 'audiocpp-sortformer',
              label: 'Sortformer v1 (audio.cpp)',
              model: repo,
              model_installed: true,
              runtime_installed: false,
              installed: false,
              reason: 'The Sortformer model is installed. Install the audio.cpp runtime to use it',
            },
          ],
        });
      if (path === '/engines/audiocpp/runtime/install/status')
        return Promise.resolve({
          installed: false,
          supported: true,
          job: { state: 'idle', progress: 0 },
        });
      if (path.startsWith('/models/licenses/details/')) {
        const id = path.slice('/models/licenses/details/'.length);
        return Promise.resolve({
          repo_id: id,
          info,
          acceptance: id === repo ? own : requiredDependency,
          history: [],
        });
      }
      return Promise.resolve({ jobs: [] });
    });
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <DiarisationSettings />
      </QueryClientProvider>,
    );

    const action = await screen.findByText('modelLicense.acceptNeeded');
    const install = screen.getByRole('button', { name: 'modelMaintenance.install' });
    fireEvent.click(action);
    const dialog = await screen.findByRole('dialog');
    expect(action.closest('button')).toHaveAccessibleName('modelLicense.acceptNeeded');
    expect(action.closest('button')).toHaveAttribute('type', 'button');
    expect(action.closest('button')).toHaveAttribute('tabindex', '0');
    await waitFor(() => expect(mock.api).toHaveBeenCalledWith(`/models/licenses/details/${repo}`));
    if (pending === 'dependency')
      await waitFor(() =>
        expect(mock.api).toHaveBeenCalledWith(`/models/licenses/details/${dependency}`),
      );
    if (pending !== 'incomplete-dependencies')
      expect(within(dialog).getByText('modelLicense.acceptNotice')).toBeInTheDocument();
    expect(mock.api.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
    // Licence review cannot install a missing executable or claim engine readiness.
    expect(screen.getByText('modelSettings.unavailable')).toBeInTheDocument();
    expect(install).toBeEnabled();
  },
);
