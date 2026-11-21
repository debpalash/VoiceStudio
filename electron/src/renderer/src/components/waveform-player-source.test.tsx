import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import { useEffect } from 'react';

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  duration: 30,
  error: null as unknown,
}));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/hooks/use-theme', () => ({ useTheme: () => ({ theme: 'dark', paletteId: 'default' }) }));
vi.mock('wavesurfer.js', () => ({ default: { create: mocks.create } }));
vi.mock('@vidstack/react', () => ({ isAudioProvider: () => true }));
vi.mock('./media-player', () => ({
  audioSource: (src: string) => ({ src }),
  audioLoaders: [],
  MediaProvider: () => null,
  useMediaState: (key: string) =>
    key === 'paused' ? true : key === 'duration' ? mocks.duration : key === 'error' ? mocks.error : 0,
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
  mocks.duration = 30;
  mocks.error = null;
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
  mocks.duration = 0;
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

it('waits for a known duration before decoding when there is no hint', () => {
  mocks.duration = 0;
  const view = render(<WaveformPlayer src="/api/audio/unknown.wav" />);
  expect(mocks.create).not.toHaveBeenCalled();
  // Metadata arrives and the clip is long: still no decode.
  mocks.duration = 3600;
  view.rerender(<WaveformPlayer src="/api/audio/unknown.wav" height={45} />);
  expect(mocks.create).not.toHaveBeenCalled();
  // Metadata arrives and the clip is short: decode now.
  mocks.duration = 20;
  view.rerender(<WaveformPlayer src="/api/audio/unknown.wav" height={46} />);
  expect(mocks.create).toHaveBeenCalledTimes(1);
});

it('observes the new container after an error unmounts and restores it', () => {
  const observed: Element[] = [];
  const disconnected: Element[] = [];
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      target: Element | null = null;
      observe(el: Element) {
        this.target = el;
        observed.push(el);
      }
      disconnect() {
        if (this.target) disconnected.push(this.target);
      }
    },
  );
  try {
    const view = render(<WaveformPlayer src="/api/audio/a.wav" />);
    expect(observed).toHaveLength(1);
    mocks.error = new Error('x');
    view.rerender(<WaveformPlayer src="/api/audio/a.wav" height={45} />);
    expect(disconnected).toContain(observed[0]);
    mocks.error = null;
    view.rerender(<WaveformPlayer src="/api/audio/a.wav" height={46} />);
    expect(observed).toHaveLength(2);
    expect(observed[1]).not.toBe(observed[0]);
    expect(observed[1].isConnected).toBe(true);
  } finally {
    vi.unstubAllGlobals();
  }
});
