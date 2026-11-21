import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import { useEffect } from 'react';

const mocks = vi.hoisted(() => ({ create: vi.fn() }));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/hooks/use-theme', () => ({ useTheme: () => ({ theme: 'dark', paletteId: 'default' }) }));
vi.mock('wavesurfer.js', () => ({ default: { create: mocks.create } }));
vi.mock('@vidstack/react', () => ({ isAudioProvider: () => true }));
vi.mock('./media-player', () => ({
  audioSource: (src: string) => ({ src }),
  audioLoaders: [],
  MediaProvider: () => null,
  useMediaState: (key: string) => (key === 'paused' ? true : 0),
  StudioMediaPlayer: ({
    children,
    src,
    onProviderChange,
  }: {
    children: React.ReactNode;
    src: { src: string };
    onProviderChange: (provider: { media: HTMLMediaElement }) => void;
  }) => {
    useEffect(() => {
      const media = document.createElement('audio');
      media.src = src.src; // what Vidstack's native provider does
      onProviderChange({ media });
    }, []);
    return <>{children}</>;
  },
}));

import { WaveformPlayer } from './waveform-player';

beforeEach(() => {
  mocks.create.mockImplementation(() => ({ on: vi.fn(), unAll: vi.fn(), destroy: vi.fn() }));
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('never gives WaveSurfer a URL that differs from the loaded media source', () => {
  render(<WaveformPlayer src="/api/audio/take-1.wav" />);

  expect(mocks.create).toHaveBeenCalledTimes(1);
  const { media, url } = mocks.create.mock.calls[0][0];
  // A mismatch makes WaveSurfer replace the element's source behind Vidstack.
  expect(url).toBe(media.currentSrc || media.src);
});

it('does not build a waveform for a clip past the decode limit', () => {
  render(<WaveformPlayer src="/api/audio/long.wav" durationHint={3600} />);
  expect(mocks.create).not.toHaveBeenCalled();
});

it('waits until the player scrolls into view before fetching and decoding', () => {
  let notify!: (entries: Array<{ isIntersecting: boolean }>) => void;
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(callback: (entries: Array<{ isIntersecting: boolean }>) => void) {
        notify = callback;
      }
      observe() {}
      disconnect() {}
    },
  );
  try {
    render(<WaveformPlayer src="/api/audio/offscreen.wav" />);
    expect(mocks.create).not.toHaveBeenCalled();
    act(() => notify([{ isIntersecting: true }]));
    expect(mocks.create).toHaveBeenCalledTimes(1);
  } finally {
    vi.unstubAllGlobals();
  }
});
