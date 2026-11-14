import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/hooks/use-theme', () => ({ useTheme: () => ({ theme: 'dark', paletteId: 'default' }) }));

import { WaveformPlayer } from './waveform-player';

beforeEach(() => {
  // jsdom has no layout observers or media decoder. Keep Vidstack's real
  // provider/state lifecycle and drive the native element's events below.
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} })),
  );
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'groupCollapsed').mockImplementation(() => {});
  vi.spyOn(console, 'trace').mockImplementation(() => {});
});
afterEach(async () => {
  await act(async () => {
    cleanup();
  });
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function nativeAudio(container: HTMLElement, source: string) {
  let audio!: HTMLAudioElement;
  await waitFor(() => {
    audio = container.querySelector('audio')!;
    expect(audio?.src).toContain(source);
  });
  return audio;
}

async function ready(audio: HTMLAudioElement) {
  Object.defineProperties(audio, {
    error: { configurable: true, value: null },
    duration: { configurable: true, value: 12 },
    readyState: { configurable: true, value: 4 },
  });
  await act(async () => {
    fireEvent.loadStart(audio);
    fireEvent.loadedMetadata(audio);
    fireEvent.canPlay(audio);
  });
}

it.each([
  { code: 2, message: 'network failed' },
  { code: 3, message: 'decode failed' },
  // Missing resources and unsupported media share code 4: it is not an HTTP 404.
  { code: 4, message: 'unsupported source' },
  { code: 4, message: 'missing resource' },
])('does not infer file deletion from $message', async (error) => {
  const { container } = render(
    <WaveformPlayer src="/api/audio/story_test.m4b" showWaveform={false} />,
  );
  const audio = await nativeAudio(container, '/api/audio/story_test.m4b');
  Object.defineProperty(audio, 'error', { configurable: true, value: error });
  await act(async () => {
    fireEvent.error(audio);
  });
  expect(await screen.findByRole('status')).toHaveTextContent('player.unavailable');
  expect(screen.queryByText('player.missing')).not.toBeInTheDocument();
  expect(screen.queryByText(error.message)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'player.play' })).toBeDisabled();
});

it('clears a previous playback failure when a new output is ready', async () => {
  const view = render(<WaveformPlayer src="/api/audio/old.m4b" showWaveform={false} />);
  const audio = await nativeAudio(view.container, '/api/audio/old.m4b');
  Object.defineProperty(audio, 'error', {
    configurable: true,
    value: { code: 3, message: 'decode failed' },
  });
  await act(async () => {
    fireEvent.error(audio);
  });
  expect(await screen.findByRole('status')).toHaveTextContent('player.unavailable');

  view.rerender(<WaveformPlayer src="/api/audio/new.mp3" showWaveform={false} />);
  const next = await nativeAudio(view.container, '/api/audio/new.mp3');
  await ready(next);
  await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  const play = screen.getByRole('button', { name: 'player.play' });
  await waitFor(() => expect(play).toBeEnabled());
  await act(async () => {
    fireEvent.click(play);
  });
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
});
