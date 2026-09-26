import { describe, it, expect, vi, beforeEach } from 'vitest';

// #653: in Tauri, decodeAudioData chokes on long-form audiobook/story renders
// (.m4b / AAC) and a blob: URL won't play in an <audio> element. The fallback
// must upload to /preview/upload and play the returned HTTP URL — NOT a blob:.
vi.mock('../utils/apiBase', () => ({
  API_BASE: 'http://127.0.0.1:3900',
  isTauriContext: () => true,
}));
vi.mock('../utils/playback', () => ({
  claimTrackedPlayback: () => ({ release: () => {}, update: () => {} }),
}));

// Imported after the mocks so media.js picks up isTauri = true at module load.
const { playBlobAudio } = await import('../utils/media');

describe('playBlobAudio fallback (#653 — long-form m4b on Tauri/WebView2)', () => {
  let audioSrcs;

  beforeEach(() => {
    audioSrcs = [];
    // AudioContext whose decodeAudioData fails like WebView2 on a big m4b.
    global.AudioContext = class {
      state = 'running';
      destination = {};
      async resume() {}
      async decodeAudioData() {
        throw new DOMException('Unable to decode audio data', 'EncodingError');
      }
      createBufferSource() {
        return { connect() {}, start() {}, stop() {} };
      }
      close() {}
    };
    global.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ url: '/preview/x.audio', audioUrl: '/preview/x.wav' }),
    }));
    global.Audio = class {
      constructor(url) {
        this.src = url;
        audioSrcs.push(url);
      }
      play() {
        return Promise.resolve();
      }
      pause() {}
    };
  });

  it('uploads to /preview/upload and plays the HTTP audioUrl (never a blob: URL)', async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3, 4])], { type: 'audio/mp4' });
    await playBlobAudio(blob);

    // Uploaded to the preview endpoint…
    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [calledUrl, opts] = global.fetch.mock.calls[0];
    expect(calledUrl).toBe('http://127.0.0.1:3900/preview/upload');
    expect(opts.method).toBe('POST');

    // …and played the HTTP URL, not a blob:.
    expect(audioSrcs).toContain('http://127.0.0.1:3900/preview/x.wav');
    expect(audioSrcs.some((u) => u.startsWith('blob:'))).toBe(false);
  });
});
