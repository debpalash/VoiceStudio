import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn(), setTier: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
vi.mock('@/hooks/use-performance-profile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/use-performance-profile')>()),
  usePerformanceProfile: () => ({
    data: { global: 'balanced', applicable_families: [], implemented_families: [] },
    isSaving: false,
    setTier: mock.setTier,
  }),
}));
import { PerformanceModelPacks } from './model-library';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('previews every pack without active engines and installs only the chosen tier on confirmation', async () => {
  mock.api.mockImplementation(async (path: string) => {
    if (path === '/models')
      return {
        disk_free_gb: 100,
        models: [
          ['k2-fsa/OmniVoice', 'OmniVoice'],
          ['Systran/faster-whisper-base', 'Whisper base'],
          ['deepdml/faster-whisper-large-v3-turbo-ct2', 'Whisper Turbo'],
          ['Systran/faster-whisper-large-v3', 'Whisper large'],
          ['facebook/nllb-200-distilled-600M', 'NLLB'],
        ].map(([repo_id, label]) => ({
          repo_id,
          label,
          role: 'ASR',
          size_gb: 1,
          supported: true,
          installed: false,
        })),
      };
    if (path === '/setup/recommendations') return { device: { label: 'CPU' } };
    if (path === '/models/install/status') return { jobs: [] };
    if (path.startsWith('/batch/jobs')) return [];
    return {};
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <PerformanceModelPacks />
    </QueryClientProvider>,
  );
  await screen.findByText('Whisper Turbo');
  for (const tier of ['fast', 'quality', 'max', 'balanced', 'fast']) {
    const button = screen.getByText('performanceProfile.' + tier).closest('button')!;
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(
      screen.getByText(
        tier === 'fast' ? 'Whisper base' : tier === 'balanced' ? 'Whisper Turbo' : 'Whisper large',
      ),
    ).toBeVisible();
  }
  expect(screen.queryByText('Whisper Turbo')).not.toBeInTheDocument();
  expect(mock.setTier).not.toHaveBeenCalled();
  expect(mock.api.mock.calls.some(([path]) => path === '/models/install')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'models.pack_install' }));
  await waitFor(() => expect(mock.setTier).toHaveBeenCalledWith({ tier: 'fast', family: null }));
  await waitFor(() =>
    expect(mock.api.mock.calls.filter(([path]) => path === '/models/install')).toHaveLength(2),
  );
  expect(mock.api).toHaveBeenCalledWith('/models/install', {
    method: 'POST',
    body: JSON.stringify({ repo_id: 'Systran/faster-whisper-base' }),
  });
});
