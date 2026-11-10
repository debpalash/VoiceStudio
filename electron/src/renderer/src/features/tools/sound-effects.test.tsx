import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import type { SfxStatus } from '@/lib/api/sfx';

const base: SfxStatus = {
  engine_id: 'moss-soundeffect',
  installed: true,
  available: true,
  supported: true,
  reason: null,
  install_allowed: true,
  download_bytes: 11230170282,
  license: 'Apache-2.0',
  min_seconds: 1,
  max_seconds: 30,
  default_seconds: 10,
  max_prompt_chars: 500,
};
const mock = vi.hoisted(() => ({
  status: null as unknown,
  generate: vi.fn(),
}));
vi.mock('@/lib/api/sfx', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/sfx')>()),
  fetchSfxStatus: async () => mock.status,
  generateSfx: mock.generate,
}));
vi.mock('@/features/settings/engine-install', () => ({
  EngineInstall: ({ id }: { id: string }) => <button type="button">install {id}</button>,
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { saveAudio: vi.fn(), revealPath: vi.fn() } }),
}));
vi.mock('@/components/waveform-player', () => ({
  WaveformPlayer: ({ src, source }: { src: string; source: string }) => (
    <div data-testid={source}>{src}</div>
  ),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key} ${JSON.stringify(values)}` : key,
  }),
}));
import { SoundEffects } from './sound-effects';

afterEach(() => {
  cleanup();
  mock.generate.mockReset();
});

function show(status: SfxStatus) {
  mock.status = status;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SoundEffects />
    </QueryClientProvider>,
  );
}

it('offers an install call-to-action with size and licence when not installed', async () => {
  show({ ...base, installed: false, available: false, reason: 'Sound effects are not installed yet.' });
  expect(await screen.findByText('sfx.install_title')).toBeTruthy();
  expect(screen.getByText(/"size":"10.5"/)).toBeTruthy();
  expect(screen.getByText(/Apache-2.0/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'install moss-soundeffect' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect((screen.getByRole('button', { name: /sfx.generate/ }) as HTMLButtonElement).disabled).toBe(true);
});

it('disables Generate and says why on an unsupported host', async () => {
  const reason = 'Sound effects need an NVIDIA GPU.';
  show({ ...base, installed: false, available: false, supported: false, reason });
  expect(await screen.findByText('sfx.unsupported_title')).toBeTruthy();
  expect(screen.getAllByText(reason).length).toBeGreaterThan(0);
  expect(screen.queryByRole('button', { name: 'install moss-soundeffect' })).toBeNull();
  fireEvent.change(screen.getByPlaceholderText('sfx.prompt_placeholder'), {
    target: { value: 'rain' },
  });
  expect((screen.getByRole('button', { name: /sfx.generate/ }) as HTMLButtonElement).disabled).toBe(true);
});

it('generates with the chosen duration and shows the player', async () => {
  mock.generate.mockResolvedValue({
    id: 'abc',
    audio_url: '/audio/abc.wav',
    duration_s: 5,
    gen_time_s: 3,
    seed: 1,
    engine: 'moss-soundeffect',
  });
  show(base);
  const prompt = await screen.findByPlaceholderText('sfx.prompt_placeholder');
  const generate = () => screen.getByRole('button', { name: /sfx.generate/ }) as HTMLButtonElement;
  await waitFor(() => expect(generate().disabled).toBe(true));
  fireEvent.change(prompt, { target: { value: ' rain on a tin roof ' } });
  fireEvent.change(screen.getByRole('slider'), { target: { value: '5' } });
  await waitFor(() => expect(generate().disabled).toBe(false));
  fireEvent.click(generate());
  expect(await screen.findByTestId('sfx-result')).toBeTruthy();
  expect(mock.generate).toHaveBeenCalledWith('rain on a tin roof', 5, expect.any(AbortSignal));
});

it('shows a clear error when generation fails and keeps the page usable', async () => {
  mock.generate.mockRejectedValue(new Error('The GPU ran out of memory'));
  show(base);
  fireEvent.change(await screen.findByPlaceholderText('sfx.prompt_placeholder'), {
    target: { value: 'thunder' },
  });
  fireEvent.click(screen.getByRole('button', { name: /sfx.generate/ }));
  expect(await screen.findByText(/ran out of memory/)).toBeTruthy();
  expect((screen.getByPlaceholderText('sfx.prompt_placeholder') as HTMLTextAreaElement).disabled).toBe(
    false,
  );
});
