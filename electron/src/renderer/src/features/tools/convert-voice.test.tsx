import { clearConversion } from './conversion-state';
import { cleanup, fireEvent, render, screen, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  queryError: false,
  retry: vi.fn(),
  convert: vi.fn(),
  ready: true,
  cloning: true as boolean | null,
  backends: [] as { available: boolean; supports_voice_conversion?: boolean }[],
  saveAudio: vi.fn(async () => ({ canceled: true })),
  profiles: null as null | { id: string; name: string; kind: string; ref_audio_path: null }[],
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { saveAudio: mock.saveAudio, revealPath: vi.fn() } }),
}));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({
    isError: mock.queryError,
    error: new Error('Engine query failed'),
    retry: mock.retry,
    data: mock.queryError ? undefined : { tts: { backends: mock.backends } },
    activeTtsReady: mock.ready,
    activeTts: { supports_cloning: mock.cloning },
  }),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock('@/lib/api/convert', () => ({ convertSpeech: mock.convert }));
vi.mock('@/hooks/use-recording', () => ({
  useRecording: () => ({ isRecording: false, isStarting: false, isCleaning: false }),
}));
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles: () => ({
    data: mock.profiles ?? [
      { id: 'a', name: 'Alpha', kind: 'clone', ref_audio_path: 'a.wav' },
      { id: 'design', name: 'Captain Crusty', kind: 'design', ref_audio_path: 'design.wav' },
      { id: 'legacy-design', name: 'Legacy Design', kind: 'design', ref_audio_path: null },
      {
        id: 'locked',
        name: 'Locked Clone',
        kind: 'clone',
        ref_audio_path: null,
        is_locked: true,
        locked_audio_path: 'locked.wav',
      },
      { id: 'empty', name: 'Empty Clone', kind: 'clone', ref_audio_path: null },
    ],
  }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/features/clone/language-picker', () => ({
  LanguagePicker: ({
    value,
    onValueChange,
    supportedOptions,
    disabled,
  }: {
    value: string;
    onValueChange: (value: string) => void;
    supportedOptions: readonly string[];
    disabled?: boolean;
  }) => (
    <select
      aria-label="convert.source_language"
      value={value}
      disabled={disabled}
      onChange={(event) => onValueChange(event.target.value)}
    >
      <option value="Auto">Auto</option>
      {supportedOptions.map((name) => (
        <option key={name} value={name}>
          {name}
        </option>
      ))}
    </select>
  ),
}));
vi.mock('@/components/waveform-player', () => ({
  WaveformPlayer: ({ src, source }: { src: string; source: string }) => (
    <div data-testid={source}>{src}</div>
  ),
}));
import { ConvertVoice } from './convert-voice';
afterEach(() => {
  cleanup();
  clearConversion();
  mock.queryError = false;
  mock.ready = true;
  mock.cloning = true;
  mock.backends = [];
  vi.clearAllMocks();
});
function mount() {
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <ConvertVoice />
    </QueryClientProvider>,
  );
  const upload = (name = 'source.wav') =>
    fireEvent.change(screen.getByLabelText('convert.source_kicker', { selector: 'input' }), {
      target: { files: [new File(['source audio'], name, { type: 'audio/wav' })] },
    });
  return { ...view, upload };
}
it('requires source and target, posts independent inputs and plays returned audio', async () => {
  mock.convert.mockResolvedValue({ id: 'x', audio_url: '/audio/x.wav', text: 'Recognized source' });
  const { upload } = mount();
  const button = screen.getByRole('button', { name: 'convert.convert' });
  expect(button).toBeDisabled();
  upload();
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  fireEvent.click(button);
  expect(await screen.findByTestId('convert-result')).toHaveTextContent('/api/audio/x.wav');
  expect(mock.convert).toHaveBeenCalledWith(
    expect.any(File),
    'a',
    true,
    expect.any(AbortSignal),
    'Auto',
    false,
  );
  expect(screen.getByText('Recognized source')).toBeInTheDocument();
});
it('sends the picked source language and offers only languages the backend pins', async () => {
  mock.convert.mockResolvedValue({ id: 'x', audio_url: '/audio/x.wav', text: 'Barev' });
  const { upload } = mount();
  const picker = screen.getByLabelText('convert.source_language');
  expect(picker).toContainHTML('Armenian');
  expect(picker).not.toContainHTML('Klingon');
  upload();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  fireEvent.change(picker, { target: { value: 'Armenian' } });
  fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
  await screen.findByTestId('convert-result');
  expect(mock.convert).toHaveBeenCalledWith(
    expect.any(File),
    'a',
    true,
    expect.any(AbortSignal),
    'Armenian',
    false,
  );
});
it('cancels obsolete conversion and ignores a late response after source changes', async () => {
  let finish!: (value: unknown) => void;
  mock.convert.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { upload } = mount();
  upload();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
  const signal = mock.convert.mock.calls[0][3];
  upload('replacement.wav');
  expect(signal.aborted).toBe(true);
  await act(async () => finish({ audio_url: '/audio/stale.wav', text: 'obsolete' }));
  expect(screen.queryByTestId('convert-result')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'convert.convert' })).toBeEnabled();
});

it('retains source and target when returning from model settings', () => {
  const first = mount();
  first.upload();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  first.unmount();
  mount();
  expect(screen.getByRole('button', { name: 'Alpha' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'source.wav' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'convert.convert' })).toBeEnabled();
});

it.each([false, null])(
  'blocks conversion for unsupported or unknown cloning capability (%s)',
  (capability) => {
    mock.cloning = capability;
    const { upload } = mount();
    upload();
    fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
    const button = screen.getByRole('button', { name: 'convert.convert' });
    expect(button).toBeDisabled();
    expect(screen.getByText('convert.cloning_required')).toBeInTheDocument();
    fireEvent.click(button);
    expect(mock.convert).not.toHaveBeenCalled();
  },
);
it('rechecks engine capability when returning from settings without losing inputs', () => {
  const first = mount();
  first.upload();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  first.unmount();
  mock.cloning = false;
  const second = mount();
  expect(screen.getByRole('button', { name: 'convert.convert' })).toBeDisabled();
  second.unmount();
  mock.cloning = true;
  mock.ready = false;
  const third = mount();
  expect(screen.getByRole('button', { name: 'convert.convert' })).toBeDisabled();
  third.unmount();
  mock.ready = true;
  mount();
  expect(screen.getByRole('button', { name: 'convert.convert' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'source.wav' })).toBeInTheDocument();
});

it('offers retry instead of model guidance when engine lookup fails', () => {
  mock.queryError = true;
  mock.ready = false;
  const { upload } = mount();
  upload();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  expect(screen.getByRole('button', { name: 'convert.convert' })).toBeDisabled();
  expect(screen.queryByText('convert.cloning_required')).not.toBeInTheDocument();
  expect(screen.getByText('Engine query failed')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'common.retry' }));
  expect(mock.retry).toHaveBeenCalledOnce();
});

it.each([
  ['Captain Crusty', 'design'],
  ['Legacy Design', 'legacy-design'],
  ['Locked Clone', 'locked'],
])('searches and converts the saved profile %s', async (name, id) => {
  mock.convert.mockResolvedValue({ id: 'x', audio_url: '/audio/x.wav', text: 'Dialogue' });
  const { upload } = mount();
  upload();
  expect(screen.queryByRole('button', { name: 'Empty Clone' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'convert.pick_voice' }), {
    target: { value: name.toLowerCase() },
  });
  expect(screen.queryByRole('button', { name: 'Alpha' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name }));
  fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
  expect(await screen.findByTestId('convert-result')).toBeInTheDocument();
  expect(mock.convert).toHaveBeenCalledWith(
    expect.any(File),
    id,
    true,
    expect.any(AbortSignal),
    'Auto',
    false,
  );
});

it('exports the converted take through the shared native save action', async () => {
  mock.convert.mockResolvedValue({ id: 'take-7', audio_url: '/audio/take-7.wav', text: 'Done' });
  const { upload } = mount();
  upload();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
  fireEvent.click(await screen.findByRole('button', { name: 'clone.download' }));
  await waitFor(() =>
    expect(mock.saveAudio).toHaveBeenCalledWith({
      url: '/api/audio/take-7.wav',
      suggestedName: 'voicestudio-take-7.wav',
    }),
  );
});
it('keep my delivery converts on an installed conversion engine without transcribing', async () => {
  // The active engine cannot clone; Keep my delivery does not need it to.
  mock.cloning = false;
  mock.backends = [{ available: true, supports_voice_conversion: true }];
  mock.convert.mockResolvedValue({ id: 'k', audio_url: '/audio/k.wav', text: '' });
  const { upload } = mount();
  fireEvent.click(screen.getByRole('switch', { name: 'convert.keep_delivery' }));
  // Transcript-only controls do not apply to a direct conversion.
  expect(screen.queryByLabelText('convert.source_language')).toBeNull();
  expect(screen.queryByText('convert.match_duration')).toBeNull();
  upload();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
  await screen.findByTestId('convert-result');
  expect(mock.convert).toHaveBeenCalledWith(
    expect.any(File),
    'a',
    true,
    expect.any(AbortSignal),
    'Auto',
    true,
  );
  expect(screen.queryByText('convert.transcript', { exact: false })).toBeNull();
});
it('keep my delivery explains the missing conversion engine', () => {
  mock.backends = [{ available: false, supports_voice_conversion: true }];
  const { upload } = mount();
  fireEvent.click(screen.getByRole('switch', { name: 'convert.keep_delivery' }));
  upload();
  fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
  expect(screen.getByText('convert.keep_delivery_engine_required')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'convert.convert' })).toBeDisabled();
});
it('keep my delivery offers only voices with a recording to convert into', () => {
  mock.backends = [{ available: true, supports_voice_conversion: true }];
  mount();
  expect(screen.getByRole('button', { name: 'Legacy Design' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('switch', { name: 'convert.keep_delivery' }));
  expect(screen.queryByRole('button', { name: 'Legacy Design' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Captain Crusty' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Locked Clone' })).toBeInTheDocument();
});
it('keep my delivery explains why no voice is offered when none has a recording', () => {
  mock.backends = [{ available: true, supports_voice_conversion: true }];
  mock.profiles = [{ id: 'd', name: 'Design Only', kind: 'design', ref_audio_path: null }];
  try {
    mount();
    expect(screen.getByRole('button', { name: 'Design Only' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'convert.keep_delivery' }));
    expect(screen.getByText('convert.keep_delivery_no_voices')).toBeInTheDocument();
    expect(screen.queryByText('clone.no_profiles')).toBeNull();
  } finally {
    mock.profiles = null;
  }
});
