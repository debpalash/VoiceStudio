import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn(), toast: { success: vi.fn() } }));
vi.mock('@/lib/api/client', () => ({
  apiJson: mock.api,
  ApiError: class ApiError extends Error {
    constructor(readonly status: number) {
      super('api');
    }
  },
}));
vi.mock('sonner', () => ({ toast: mock.toast }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key} ${JSON.stringify(params)}` : key,
  }),
}));

import { GpuOfferBanner, type GpuOfferStatus } from './gpu-offer';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const offer: GpuOfferStatus = {
  eligible: true,
  answer: null,
  active: false,
  show: true,
  licence_accepted: false,
  gpu_name: 'AMD Radeon 890M',
  device: 'Vulkan0',
  shared_memory: true,
  effective_vram_gb: 15.65,
  quant: 'BF16',
  download_mb: 1600,
};

function renderBanner() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GpuOfferBanner />
    </QueryClientProvider>,
  );
}

it('names the GPU, quant, download size and licence, and accepts on Use GPU', async () => {
  mock.api.mockImplementation((_path: string, init?: RequestInit) =>
    Promise.resolve(init?.method === 'POST' ? { ...offer, show: false, active: true } : offer),
  );
  renderBanner();
  expect(await screen.findByText(/gpuOffer\.body_shared.*AMD Radeon 890M.*BF16/)).toBeTruthy();
  expect(screen.getByText(/gpuOffer\.download.*1600/)).toBeTruthy();
  expect(screen.getByText('gpuOffer.licence')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /gpuOffer\.accept/ }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/engines/omnivoice-gguf/gpu-offer',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ accept: true }) }),
    ),
  );
  await waitFor(() => expect(screen.queryByText('gpuOffer.title')).toBeNull());
  expect(mock.toast.success).toHaveBeenCalled();
});

it('records Not now and hides', async () => {
  mock.api.mockImplementation((_path: string, init?: RequestInit) =>
    Promise.resolve(
      init?.method === 'POST' ? { ...offer, show: false, answer: 'declined' } : offer,
    ),
  );
  renderBanner();
  fireEvent.click((await screen.findAllByRole('button', { name: 'gpuOffer.decline' }))[0]);
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/engines/omnivoice-gguf/gpu-offer',
      expect.objectContaining({ body: JSON.stringify({ accept: false }) }),
    ),
  );
  await waitFor(() => expect(screen.queryByText('gpuOffer.title')).toBeNull());
});

it('keeps the banner and shows an error when Not now cannot be saved', async () => {
  mock.api.mockImplementation((_path: string, init?: RequestInit) =>
    init?.method === 'POST' ? Promise.reject(new Error('offline')) : Promise.resolve(offer),
  );
  renderBanner();
  fireEvent.click((await screen.findAllByRole('button', { name: 'gpuOffer.decline' }))[0]);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.getByText('gpuOffer.title')).toBeTruthy();
});

it('reports a failure instead of a switch when the download cannot start', async () => {
  mock.api.mockImplementation((_path: string, init?: RequestInit) =>
    Promise.resolve(
      init?.method === 'POST' ? { ...offer, active: false, download: { status: 'failed' } } : offer,
    ),
  );
  renderBanner();
  fireEvent.click(await screen.findByRole('button', { name: /gpuOffer\.accept/ }));
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.getByText('gpuOffer.title')).toBeTruthy();
  expect(mock.toast.success).not.toHaveBeenCalled();
});

it('stays hidden when the host is not eligible', async () => {
  mock.api.mockResolvedValue({ ...offer, eligible: false, show: false });
  renderBanner();
  await waitFor(() => expect(mock.api).toHaveBeenCalled());
  expect(screen.queryByText('gpuOffer.title')).toBeNull();
});
