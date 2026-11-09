import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key} ${JSON.stringify(params)}` : key,
  }),
}));

import { GgufQuantPicker, type GgufQuantStatus } from './gguf-quant-picker';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const option = (tag: string, size: number, installed: boolean) => ({
  quant: `omnivoice-base-${tag}.gguf`,
  tag,
  approx_size_mb: size,
  installed,
  recommended: tag === 'BF16',
});

function status(overrides: Partial<GgufQuantStatus> = {}): GgufQuantStatus {
  return {
    runtime: { ready: true, problem: null, vulkan_module: true },
    hardware: {
      backend: 'vulkan',
      device: 'Vulkan0',
      gpu_name: 'AMD Radeon 890M',
      shared_memory: true,
      effective_vram_gb: 15.65,
    },
    recommended: 'omnivoice-base-BF16.gguf',
    selection: 'auto',
    selection_source: 'auto',
    env_var: 'OMNIVOICE_GGUF_QUANT',
    selected: 'omnivoice-base-BF16.gguf',
    in_use: 'omnivoice-base-Q4_K_M.gguf',
    options: [option('Q4_K_M', 659, true), option('Q8_0', 945, false), option('BF16', 1600, false)],
    download: { state: 'idle', quant: null, error: null, bytes_total: 0 },
    ...overrides,
  };
}

function renderPicker() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GgufQuantPicker />
    </QueryClientProvider>,
  );
}

it('shows the automatic quant with the Vulkan GPU and offers its download', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(path.endsWith('/quants') ? status() : { status: 'started' }),
  );
  renderPicker();
  expect(await screen.findAllByText(/ggufQuant\.auto.*"quant":"BF16"/)).not.toHaveLength(0);
  expect(screen.getByText(/ggufQuant\.using_installed.*Q4_K_M/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /ggufQuant\.download/ }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/engines/omnivoice-gguf/quants/download',
      expect.objectContaining({ body: JSON.stringify({ quant: 'omnivoice-base-BF16.gguf' }) }),
    ),
  );
});

it('names a missing runtime apart from missing weights', async () => {
  mock.api.mockResolvedValue(
    status({
      runtime: { ready: false, problem: 'missing', vulkan_module: false },
      in_use: null,
    }),
  );
  renderPicker();
  expect(await screen.findByText('ggufQuant.runtime_missing')).toBeTruthy();
  expect(screen.queryByText('ggufQuant.weights_missing')).toBeNull();
});

it('locks the picker when the environment pins a quant', async () => {
  mock.api.mockResolvedValue(
    status({ selection: 'omnivoice-base-Q8_0.gguf', selection_source: 'env' }),
  );
  renderPicker();
  expect(await screen.findByText(/ggufQuant\.env_pinned.*OMNIVOICE_GGUF_QUANT/)).toBeTruthy();
});
