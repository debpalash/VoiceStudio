import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Global mini-player wiring of playBlobAudio (browser path): every playback
// must register as a *tracked* 'output' claim — label, timeupdate-driven
// currentTime/duration, real seek/pause/resume — and report exactly one
// onDone with the right reason ('ended' | 'stopped' | 'error') so callers
// (story chains, gallery cards) can advance or reset their state.
vi.mock('../utils/apiBase', () => ({
  API_BASE: 'http://127.0.0.1:3900',
  isTauriContext: () => false,
}));

const { playBlobAudio, computePeaks } = await import('../utils/media');
const { getPlaybackTrack, seekActivePlayback, pauseActivePlayback, stopActivePlayback } =
  await import('../utils/playback');

let audios;

class FakeAudio {
  constructor(url) {
    this.src = url;
    this.currentTime = 0;
    this.duration = NaN;
    this.paused = true;
    this._listeners = {};
    audios.push(this);
  }
  addEventListener(ev, fn) {
    (this._listeners[ev] ||= []).push(fn);
  }
  emit(ev) {
    for (const fn of this._listeners[ev] || []) fn();
  }
  play() {
    this.paused = false;
    this.emit('play');
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
    this.emit('pause');
  }
}

beforeEach(() => {
  audios = [];
  global.Audio = FakeAudio;
  URL.createObjectURL = vi.fn(() => `blob:mock-${audios.length}`);
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  stopActivePlayback();
});

const wav = () => new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/wav' });

describe('playBlobAudio → tracked global playback (browser path)', () => {
  it('claims a tracked output playback carrying the label + transport', async () => {
    await playBlobAudio(wav(), { label: 'Aria (profile)' });
    expect(getPlaybackTrack()).toMatchObject({
      source: 'output',
      label: 'Aria (profile)',
      canSeek: true,
      canPause: true,
    });
  });

  it('timeupdate events drive the track snapshot', async () => {
    await playBlobAudio(wav());
    const a = audios[0];
    a.currentTime = 1.5;
    a.duration = 4;
    a.emit('timeupdate');
    expect(getPlaybackTrack()).toMatchObject({ currentTime: 1.5, duration: 4 });
  });

  it('seekActivePlayback moves the element clock', async () => {
    await playBlobAudio(wav());
    const a = audios[0];
    a.duration = 10;
    seekActivePlayback(6.25);
    expect(a.currentTime).toBe(6.25);
    // Clamped into [0, duration].
    seekActivePlayback(99);
    expect(a.currentTime).toBe(10);
    seekActivePlayback(-3);
    expect(a.currentTime).toBe(0);
  });

  it('pause keeps the claim (bar stays) instead of releasing it', async () => {
    await playBlobAudio(wav());
    pauseActivePlayback();
    expect(audios[0].paused).toBe(true);
    expect(getPlaybackTrack()).toMatchObject({ paused: true, source: 'output' });
  });

  it('natural end releases the claim, revokes the URL, onDone("ended") once', async () => {
    const onDone = vi.fn();
    await playBlobAudio(wav(), { onDone });
    audios[0].emit('ended');
    expect(getPlaybackTrack()).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith('ended');
    // A late stop must not double-fire onDone.
    stopActivePlayback();
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('manager stop pauses the element, cleans up, onDone("stopped")', async () => {
    const onDone = vi.fn();
    await playBlobAudio(wav(), { onDone });
    stopActivePlayback();
    expect(audios[0].paused).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith('stopped');
    expect(getPlaybackTrack()).toBeNull();
  });

  it('single-playback invariant: a second play stops the first', async () => {
    const first = vi.fn();
    await playBlobAudio(wav(), { label: 'first', onDone: first });
    await playBlobAudio(wav(), { label: 'second' });
    expect(first).toHaveBeenCalledWith('stopped');
    expect(audios[0].paused).toBe(true);
    expect(getPlaybackTrack()).toMatchObject({ label: 'second' });
  });

  it('a failed play() releases the claim and reports onDone("error")', async () => {
    const onDone = vi.fn();
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const realPlay = FakeAudio.prototype.play;
    FakeAudio.prototype.play = function () {
      return Promise.reject(new Error('NotAllowedError'));
    };
    try {
      await playBlobAudio(wav(), { onDone });
      await Promise.resolve(); // let the rejection handler run
      expect(onDone).toHaveBeenCalledWith('error');
      expect(getPlaybackTrack()).toBeNull();
    } finally {
      FakeAudio.prototype.play = realPlay;
      err.mockRestore();
    }
  });
});

describe('computePeaks', () => {
  const fakeBuffer = (data, channels = 1) => ({
    numberOfChannels: channels,
    length: data.length,
    getChannelData: () => data,
  });

  it('downsamples into normalized [0..1] buckets', () => {
    const data = new Float32Array(4800);
    for (let i = 0; i < data.length; i++) data[i] = Math.sin(i / 50) * 0.5;
    const peaks = computePeaks(fakeBuffer(data));
    expect(peaks).toHaveLength(240);
    expect(Math.max(...peaks)).toBeCloseTo(1); // normalized to the loudest bucket
    expect(peaks.every((p) => p >= 0 && p <= 1)).toBe(true);
  });

  it('short buffers yield one bucket per sample', () => {
    const peaks = computePeaks(fakeBuffer(new Float32Array([0.1, -0.8, 0.4])));
    expect(peaks).toHaveLength(3);
    expect(peaks[1]).toBeCloseTo(1); // |-0.8| is the max
  });

  it('returns null for empty/invalid buffers instead of throwing', () => {
    expect(computePeaks(fakeBuffer(new Float32Array(0)))).toBeNull();
    expect(computePeaks(null)).toBeNull();
  });
});
