import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import enLocale from '../../i18n/locales/en.json';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
// Render the real English strings so the test pins what a user reads.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params: Record<string, string | number> = {}) => {
      const value = key
        .split('.')
        .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], enLocale);
      return typeof value === 'string'
        ? value.replace(/\{\{(\w+)\}\}/g, (_, name) => String(params[name] ?? ''))
        : key;
    },
  }),
}));
import { GpuAcceleration } from './gpu-acceleration';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const amdWindows = {
  platform: 'win32',
  gpus: [{ vendor: 'amd', name: 'AMD Radeon RX 9070 XT', vram_gb: 16 }],
  torch: { kind: 'cuda', version: '2.8.0+cu128' },
  state: 'amd_cuda_build',
  params: { gpu: 'AMD Radeon RX 9070 XT' },
  options: ['vulkan_engines', 'rocm_windows_manual'],
  engines: [
    {
      id: 'omnivoice',
      name: 'OmniVoice',
      kind: 'tts',
      available: true,
      code: 'host_gpu_unusable_rocm',
      params: {},
      reason: null,
    },
    {
      id: 'audiocpp',
      name: 'audio.cpp',
      kind: 'tts',
      available: true,
      code: 'gpu',
      params: { device: 'vulkan' },
      reason: null,
    },
    {
      id: 'x',
      name: 'Future engine',
      kind: 'asr',
      available: true,
      code: 'some_new_backend_code',
      params: {},
      reason: null,
    },
  ],
};

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <GpuAcceleration />
    </QueryClientProvider>,
  );
}

it('tells a Radeon owner why the GPU is idle and which engines can still use it', async () => {
  mock.api.mockResolvedValue(amdWindows);
  renderIt();
  expect(await screen.findByText(/cannot drive AMD GPUs/)).toHaveTextContent(
    'AMD Radeon RX 9070 XT was found, but this install uses the NVIDIA CUDA build of PyTorch (2.8.0+cu128)',
  );
  expect(screen.getByText(/audio\.cpp runs on AMD Radeon GPUs through Vulkan/)).toBeInTheDocument();
  expect(screen.getByText(/not installed by VoiceStudio/)).toBeInTheDocument();
  // Unknown future codes are hidden rather than rendered as raw keys; 1 of 2 known engines is on the GPU.
  expect(screen.getByText('1 of 2 engines use the GPU')).toBeInTheDocument();
  expect(screen.getByText('Uses the GPU (vulkan)')).toBeInTheDocument();
  expect(
    screen.getByText('CPU: would use your AMD GPU with a ROCm build of PyTorch'),
  ).toBeInTheDocument();
  expect(screen.queryByText(/settings\.gpu_report/)).not.toBeInTheDocument();
  expect(mock.api).toHaveBeenCalledWith('/api/settings/gpu-report', expect.anything());
});

it('renders nothing for an empty payload', async () => {
  mock.api.mockResolvedValueOnce({});
  renderIt();
  await waitFor(() => expect(mock.api).toHaveBeenCalled());
  expect(document.body).not.toHaveTextContent('GPU acceleration');
});

it('shows a quiet note when the report cannot be read', async () => {
  mock.api.mockRejectedValueOnce(new Error('boom'));
  renderIt();
  expect(
    await screen.findByText('Could not read GPU details from the backend.'),
  ).toBeInTheDocument();
});
