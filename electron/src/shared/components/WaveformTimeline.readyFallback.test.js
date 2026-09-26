import React from 'react';
import { act, render, waitFor, screen, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const wave = vi.hoisted(() => ({ handlers: {}, instance: null }));

vi.mock('wavesurfer.js', () => ({
  default: {
    create: vi.fn(() => {
      const parent = document.createElement('div');
      const wrapper = document.createElement('div');
      parent.appendChild(wrapper);
      Object.defineProperty(wrapper, 'scrollWidth', { configurable: true, value: 2300 });
      wave.handlers = {};
      wave.instance = {
        on: vi.fn((event, handler) => {
          (wave.handlers[event] ||= []).push(handler);
          return () => {};
        }),
        un: vi.fn(),
        load: vi.fn(() => Promise.resolve()),
        setMediaElement: vi.fn(),
        getDuration: vi.fn(() => 23),
        getWrapper: vi.fn(() => wrapper),
        zoom: vi.fn(),
        pause: vi.fn(),
        destroy: vi.fn(),
        cancelAudioFetch: vi.fn(),
      };
      return wave.instance;
    }),
  },
}));

vi.mock('wavesurfer.js/dist/plugins/minimap.esm.js', () => ({
  default: { create: vi.fn(() => ({})) },
}));
vi.mock('wavesurfer.js/dist/plugins/timeline.esm.js', () => ({
  default: { create: vi.fn(() => ({})) },
}));

import WaveformTimeline from './WaveformTimeline';

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

function emitWave(event, value) {
  for (const handler of wave.handlers[event] || []) handler(value);
}

describe('WaveformTimeline audible error recovery (#1692)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(16) })),
    );
    const decoded = {
      duration: 23,
      getChannelData: () => new Float32Array([0, 0.5, -0.5, 0]),
    };
    vi.stubGlobal(
      'AudioContext',
      class {
        async decodeAudioData() {
          return decoded;
        }
      },
    );
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  });

  it('enables native playback as soon as metadata arrives, before waveform ready', async () => {
    const { container } = render(
      React.createElement(WaveformTimeline, { audioSrc: '/audio.wav', videoSrc: '/video.mp4' }),
    );
    const video = container.querySelector('video');
    expect(screen.getByRole('button', { name: 'Play' })).toBeDisabled();
    Object.defineProperty(video, 'duration', { configurable: true, value: 1980.4 });
    fireEvent.loadedMetadata(video);
    expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled();
    expect(screen.getByText(/33:00.4/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    await waitFor(() => expect(video.play).toHaveBeenCalled());
  });

  it('loads unequal-duration peaks from the selected dub and synchronizes its video', async () => {
    let releaseFetch;
    fetch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseFetch = () => resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(16) });
        }),
    );
    const { container } = render(
      React.createElement(WaveformTimeline, {
        audioSrc: 'http://localhost/original.wav',
        videoSrc: 'http://localhost/dubbed.mp4',
        playbackFallbackSrc: 'http://localhost/dubbed-es.wav',
      }),
    );
    const video = container.querySelector('video');
    Object.defineProperty(video, 'duration', { configurable: true, value: 61 });

    act(() => emitWave('error', new DOMException('video decode failed', 'NotSupportedError')));

    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    act(() => emitWave('error', new Error('duplicate original-video error')));
    expect(container.querySelector('.wfm-error')).not.toBeInTheDocument();
    await act(async () => releaseFetch());
    await waitFor(() => expect(wave.instance.setMediaElement).toHaveBeenCalledOnce());
    const fallbackAudio = wave.instance.setMediaElement.mock.calls[0][0];
    expect(fallbackAudio).toBeInstanceOf(HTMLAudioElement);
    expect(fallbackAudio.src).toBe('http://localhost/dubbed-es.wav');
    expect(fetch).toHaveBeenCalledWith('http://localhost/dubbed-es.wav');
    expect(wave.instance.load).toHaveBeenCalledWith(
      'http://localhost/dubbed-es.wav',
      [expect.any(Float32Array)],
      23,
    );
    expect(wave.instance.load.mock.calls[0][0]).not.toBeUndefined();

    fallbackAudio.currentTime = 7;
    act(() => fallbackAudio.dispatchEvent(new Event('seeking')));
    expect(video.currentTime).toBe(7);

    act(() => fallbackAudio.dispatchEvent(new Event('play')));
    expect(video.muted).toBe(true);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();

    act(() => fallbackAudio.dispatchEvent(new Event('pause')));
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  });

  it('makes a rejected companion terminal instead of recursively reloading it', async () => {
    render(
      React.createElement(WaveformTimeline, {
        audioSrc: 'http://localhost/original.wav',
        videoSrc: 'http://localhost/dubbed.mp4',
        playbackFallbackSrc: 'http://localhost/missing-dub.wav',
      }),
    );
    wave.instance.load.mockRejectedValueOnce(new Error('companion rejected'));

    act(() => emitWave('error', new Error('initial decode failed')));

    await waitFor(() => expect(document.querySelector('.wfm-error')).toBeInTheDocument());
    act(() => emitWave('error', new Error('companion rejected')));
    expect(wave.instance.load).toHaveBeenCalledOnce();
  });
});
