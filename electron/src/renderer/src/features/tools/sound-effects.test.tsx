import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api/client';
import type { SfxStatus } from '@/lib/api/sfx';

const base: SfxStatus = {
  engine_id: 'moss-soundeffect',
  installed: true,
  available: true,
  supported: true,
  reason: null,
  reason_code: null,
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
  show({
    ...base,
    installed: false,
    available: false,
    reason: 'Sound effects are not installed yet.',
    reason_code: 'not_installed',
  });
  expect(await screen.findByText('sfx.install_title')).toBeTruthy();
  expect(screen.getByText(/"size":"10.5"/)).toBeTruthy();
  expect(screen.getByText(/Apache-2.0/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'install moss-soundeffect' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect((screen.getByRole('button', { name: /sfx.generate/ }) as HTMLButtonElement).disabled).toBe(true);
});

it('disables Generate and says why on an unsupported host', async () => {
  const reason = 'Sound effects need an NVIDIA GPU.';
  show({
    ...base,
    installed: false,
    available: false,
    supported: false,
    reason,
    reason_code: 'host_unsupported',
  });
  expect(await screen.findByText('sfx.unsupported_title')).toBeTruthy();
  expect(screen.getAllByText('sfx.reason_host_unsupported').length).toBeGreaterThan(0);
  expect(screen.queryByText(reason)).toBeNull();
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

it.each([
  ['not_installed', 'sfx.reason_not_installed'],
  ['weights_missing', 'sfx.reason_weights_missing'],
  ['sidecar_missing', 'sfx.reason_sidecar_missing'],
  ['something_new', 'sfx.reason_unknown'],
])('translates the status reason code %s and never shows the server text', async (code, key) => {
  const reason = 'English server text that must not be shown';
  show({ ...base, installed: false, available: false, reason, reason_code: code });
  expect((await screen.findAllByText(key)).length).toBeGreaterThan(0);
  expect(screen.queryByText(reason)).toBeNull();
});

async function failWith(error: unknown) {
  mock.generate.mockRejectedValue(error);
  show(base);
  fireEvent.change(await screen.findByPlaceholderText('sfx.prompt_placeholder'), {
    target: { value: 'thunder' },
  });
  fireEvent.click(screen.getByRole('button', { name: /sfx.generate/ }));
}

const apiError = (status: number, code: string) =>
  new ApiError(status, 'English server detail', {
    detail: { code, error: code, message: 'English server detail' },
  });

it.each([
  ['sfx_prompt_required', 'sfx.prompt_required'],
  ['sfx_prompt_too_long', 'sfx.error_prompt_too_long'],
  ['sfx_bad_duration', 'sfx.error_bad_duration'],
  ['sfx_unsupported_host', 'sfx.reason_host_unsupported'],
  ['sfx_not_installed', 'sfx.reason_not_installed'],
  ['sfx_busy', 'sfx.error_busy'],
  ['sfx_timeout', 'sfx.error_timeout'],
  ['sfx_out_of_memory', 'sfx.error_out_of_memory'],
  ['sfx_failed', 'sfx.failed'],
])('renders the translated message for generate error %s', async (code, key) => {
  await failWith(apiError(500, code));
  expect(await screen.findByText(new RegExp(`^${key}`))).toBeTruthy();
  expect(screen.queryByText(/English server detail/)).toBeNull();
  expect((screen.getByPlaceholderText('sfx.prompt_placeholder') as HTMLTextAreaElement).disabled).toBe(
    false,
  );
});

it('falls back to the generic translated message for an unknown code or a plain error', async () => {
  await failWith(apiError(500, 'sfx_from_the_future'));
  expect(await screen.findByText(/^sfx.failed/)).toBeTruthy();
  expect(screen.queryByText(/English server detail/)).toBeNull();
  cleanup();
  mock.generate.mockReset();
  await failWith(new Error('The GPU ran out of memory'));
  expect(await screen.findByText(/^sfx.failed/)).toBeTruthy();
  expect(screen.queryByText(/ran out of memory/)).toBeNull();
});
