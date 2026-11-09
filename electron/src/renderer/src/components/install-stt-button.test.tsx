import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import '@/i18n';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  navigate: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: mock.api,
  describeError: (error: unknown) => String(error),
}));
vi.mock('@/router', () => ({ router: { navigate: mock.navigate } }));
vi.mock('sonner', () => ({ toast: { success: mock.success, error: mock.error } }));
import { InstallSttButton } from './install-stt-button';

const renderButton = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InstallSttButton />
    </QueryClientProvider>,
  );

beforeEach(() => {
  mock.api.mockResolvedValue({});
});
afterEach(() => vi.clearAllMocks());

it('downloads the first recommended speech-to-text model that is not installed', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/setup/recommendations'
        ? {
            models: [
              { repo_id: 'tts/model', role: 'TTS', installed: false },
              { repo_id: 'asr/installed', role: 'ASR', installed: true },
              { repo_id: 'asr/wanted', role: 'ASR', installed: false },
            ],
          }
        : {},
    ),
  );
  renderButton();
  fireEvent.click(screen.getByRole('button', { name: /speech-to-text/i }));
  await waitFor(() => expect(mock.success).toHaveBeenCalled());
  expect(mock.api).toHaveBeenCalledWith('/models/install', {
    method: 'POST',
    body: JSON.stringify({ repo_id: 'asr/wanted' }),
  });
});

it('opens the Model Catalogue when every recommended model is already installed', async () => {
  mock.api.mockResolvedValue({ models: [{ repo_id: 'asr/a', role: 'ASR', installed: true }] });
  renderButton();
  fireEvent.click(screen.getByRole('button', { name: /speech-to-text/i }));
  await waitFor(() => expect(mock.navigate).toHaveBeenCalledWith({
      to: '/settings/models/$family',
      params: { family: 'asr' },
    }),
  );
  expect(mock.api).not.toHaveBeenCalledWith('/models/install', expect.anything());
});

it('reports a failed install request instead of failing silently', async () => {
  mock.api.mockImplementation(async () => {
    throw new Error('offline');
  });
  renderButton();
  fireEvent.click(screen.getByRole('button', { name: /speech-to-text/i }));
  await waitFor(() => expect(mock.error).toHaveBeenCalled());
});
