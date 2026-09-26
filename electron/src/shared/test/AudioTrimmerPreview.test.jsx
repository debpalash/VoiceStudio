import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// Regression guard for #1210: the AudioTrimmer preview must play the SELECTED
// region of the decoded buffer — the exact same buffer + [start,end] window the
// waveform is drawn from and the confirmed clip is sliced from. The old preview
// seeked a second <audio> element on the original file, treating the selection's
// decoded-buffer-seconds as the file's container-seconds; for VBR /
// mis-reported-duration clips those timelines diverge, so the preview played a
// different region than the one selected and exported. This asserts preview is
// wired to the buffer timeline (a BufferSource started at the selection start),
// which fails against the old media-element implementation.

const DECODED_DURATION = 8;
const SAMPLE_RATE = 22050;

function makeBuffer() {
  const length = Math.round(DECODED_DURATION * SAMPLE_RATE);
  return {
    duration: DECODED_DURATION,
    sampleRate: SAMPLE_RATE,
    length,
    numberOfChannels: 1,
    getChannelData: () => new Float32Array(length),
  };
}

let startedSources;

class MockBufferSource {
  constructor() {
    this.buffer = null;
    this.loop = false;
    this.loopStart = 0;
    this.loopEnd = 0;
    this.onended = null;
    this.startArgs = null;
  }
  connect() {}
  start(...args) {
    this.startArgs = args;
    startedSources.push(this);
  }
  stop() {}
}

class MockAudioContext {
  constructor() {
    this.state = 'running';
    this.currentTime = 0;
    this.destination = {};
  }
  resume() {
    return Promise.resolve();
  }
  createBufferSource() {
    return new MockBufferSource();
  }
  close() {}
}

class MockOfflineAudioContext {
  constructor() {}
  decodeAudioData() {
    return Promise.resolve(makeBuffer());
  }
}

class MockAudio {
  constructor() {
    this.preload = '';
    this.duration = DECODED_DURATION;
  }
  addEventListener(ev, cb) {
    if (ev === 'loadedmetadata') queueMicrotask(cb);
  }
  set src(_) {}
}

function stubCanvas() {
  const ctx = new Proxy(
    {},
    {
      get: () => () => {},
      set: () => true,
    },
  );
  return vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx);
}

import AudioTrimmer from '../components/AudioTrimmer.jsx';

describe('AudioTrimmer preview (#1210)', () => {
  let canvasSpy;
  beforeEach(() => {
    startedSources = [];
    canvasSpy = stubCanvas();
    vi.stubGlobal('AudioContext', MockAudioContext);
    vi.stubGlobal('Audio', MockAudio);
    window.OfflineAudioContext = MockOfflineAudioContext;
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: () => 'blob:mock',
      revokeObjectURL: () => {},
    });
  });
  afterEach(() => {
    canvasSpy.mockRestore();
    vi.unstubAllGlobals();
    delete window.OfflineAudioContext;
    vi.restoreAllMocks();
  });

  it('previews the selected buffer region, not a second media timeline', async () => {
    const file = new File([new Uint8Array([1, 2, 3, 4])], 'ref.wav', { type: 'audio/wav' });
    render(<AudioTrimmer file={file} maxSeconds={15} onConfirm={() => {}} onCancel={() => {}} />);

    const playBtn = await screen.findByRole('button', { name: /preview selection/i });
    await waitFor(() => expect(playBtn).not.toBeDisabled());

    // Select [2.5, 5.5] via the numeric fields (no canvas geometry needed).
    const [startInput, endInput] = screen.getAllByRole('textbox');
    fireEvent.change(startInput, { target: { value: '2.5' } });
    fireEvent.blur(startInput);
    fireEvent.change(endInput, { target: { value: '5.5' } });
    fireEvent.blur(endInput);

    fireEvent.click(playBtn);

    await waitFor(() => expect(startedSources.length).toBe(1));
    const src = startedSources[0];
    // Playback is anchored at the selection START on the buffer timeline …
    expect(src.startArgs[1]).toBeCloseTo(2.5, 6);
    // … and (loop defaults on) loops exactly the selected window.
    expect(src.loop).toBe(true);
    expect(src.loopStart).toBeCloseTo(2.5, 6);
    expect(src.loopEnd).toBeCloseTo(5.5, 6);
  });
});
