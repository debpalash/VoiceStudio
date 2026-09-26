import i18n from 'i18next';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Streaming TTS preview (feat: streaming-tts-preview): the NDJSON client must
// start Web Audio playback from the FIRST chunk, register as a tracked
// 'output' playback (mini-player bar), flip its label on completion, resolve
// with the "done" metadata — and turn ANY mid-stream failure into
// StreamingPreviewError so useTTS can fall back to the classic flow.

vi.mock('../api/client', () => ({
  apiFetch: vi.fn(),
}));

const { apiFetch } = await import('../api/client');
const {
  streamGenerateSpeech,
  createStreamingChunkPlayer,
  supportsStreamingPreview,
  resolveRemoteTtsTarget,
  decodePcm16Base64,
  pcm16BytesToFloat32,
  peaksFromChunkList,
  StreamingPreviewError,
  shouldFallbackToClassic,
} = await import('../utils/streamingTts');
const { getPlaybackTrack, stopActivePlayback, seekActivePlayback } =
  await import('../utils/playback');

// ── Web Audio fakes ─────────────────────────────────────────────────────────

class FakeGainParam {
  setValueAtTime() {}
  linearRampToValueAtTime() {}
}
class FakeGain {
  constructor() {
    this.gain = new FakeGainParam();
  }
  connect() {}
  disconnect() {}
}
class FakeSource {
  constructor(ctx) {
    this.ctx = ctx;
  }
  connect() {}
  disconnect() {}
  start(when, offset) {
    this.startedAt = { when, offset: offset || 0 };
    this.ctx.started.push(this);
  }
  stop() {
    this.stopped = true;
  }
}
class FakeAudioContext {
  static instances = [];
  constructor() {
    this.currentTime = 0;
    this.state = 'running';
    this.destination = {};
    this.started = []; // every source that called start()
    FakeAudioContext.instances.push(this);
  }
  resume() {
    this.state = 'running';
    return Promise.resolve();
  }
  suspend() {
    this.state = 'suspended';
    return Promise.resolve();
  }
  close() {
    this.state = 'closed';
  }
  createBuffer(channels, length, sampleRate) {
    const data = new Float32Array(length);
    return {
      length,
      duration: length / sampleRate,
      numberOfChannels: channels,
      sampleRate,
      copyToChannel: (src) => data.set(src),
      getChannelData: () => data,
    };
  }
  createBufferSource() {
    return new FakeSource(this);
  }
  createGain() {
    return new FakeGain();
  }
}

// ── NDJSON fixtures ─────────────────────────────────────────────────────────

const b64Pcm = (samples) => {
  // Int16 PCM little-endian → base64.
  const pcm = new Int16Array(samples);
  const bytes = new Uint8Array(pcm.buffer);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
};

const CHUNK_SAMPLES = 2400; // 100 ms @ 24 kHz

const startEvent = (totalChunks) => ({
  type: 'start',
  sample_rate: 24000,
  channels: 1,
  format: 'pcm16',
  total_chunks: totalChunks,
  crossfade_ms: 50,
  seed: 7,
});
const chunkEvent = (seq) => ({
  type: 'chunk',
  seq,
  pcm: b64Pcm(Array.from({ length: CHUNK_SAMPLES }, (_, i) => (i % 100) * 50)),
});
const doneEvent = {
  type: 'done',
  id: 'abc12345',
  audio_path: 'abc12345.wav',
  duration: 0.3,
  gen_time: 1.2,
  seed: 7,
  sample_rate: 24000,
};

const ndjsonResponse = (events, headers = {}) => {
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const e of events) controller.enqueue(enc.encode(JSON.stringify(e) + '\n'));
      controller.close();
    },
  });
  return { headers: new Headers(headers), body: stream };
};

beforeEach(() => {
  FakeAudioContext.instances = [];
  window.AudioContext = FakeAudioContext;
  apiFetch.mockReset();
});

afterEach(() => {
  stopActivePlayback();
});

// ── unit: codecs ────────────────────────────────────────────────────────────

describe('decodePcm16Base64', () => {
  it('roundtrips int16 samples to normalized floats', () => {
    const out = decodePcm16Base64(b64Pcm([0, 16384, -16384, 32767, -32768]));
    expect(out.length).toBe(5);
    expect(out[0]).toBe(0);
    expect(out[1]).toBeCloseTo(0.5, 3);
    expect(out[2]).toBeCloseTo(-0.5, 3);
    expect(out[3]).toBeCloseTo(1, 2);
    expect(out[4]).toBe(-1);
  });
});

describe('pcm16BytesToFloat32', () => {
  it('decodes a raw binary frame (the /ws/tts shape) identically to base64', () => {
    const samples = [0, 16384, -16384, 32767, -32768];
    const raw = pcm16BytesToFloat32(new Int16Array(samples).buffer);
    const viaB64 = decodePcm16Base64(b64Pcm(samples));
    expect(Array.from(raw)).toEqual(Array.from(viaB64));
  });

  it('decodes a view carved at an odd byteOffset instead of throwing', () => {
    const backing = new Uint8Array(5);
    backing.set(new Uint8Array(new Int16Array([-12345]).buffer), 1);
    const out = pcm16BytesToFloat32(backing.subarray(1, 3));
    expect(out.length).toBe(1);
    expect(out[0]).toBeCloseTo(-12345 / 32768, 5);
  });

  it('respects a Uint8Array view with a nonzero byteOffset', () => {
    const backing = new Uint8Array(8);
    backing.set(new Uint8Array(new Int16Array([12345]).buffer), 2);
    const out = pcm16BytesToFloat32(backing.subarray(2, 4));
    expect(out.length).toBe(1);
    expect(out[0]).toBeCloseTo(12345 / 32768, 5);
  });
});

describe('createStreamingChunkPlayer.appendPcm16Bytes', () => {
  it('schedules raw /ws/tts frames on the same gapless timeline as base64 chunks', () => {
    const player = createStreamingChunkPlayer({ label: 'live', sampleRate: 24000 });
    const ctx = FakeAudioContext.instances.at(-1);

    const frame = new Int16Array(Array.from({ length: 2400 }, (_, i) => (i % 100) * 50));
    player.appendPcm16Bytes(frame.buffer);
    player.appendPcm16Bytes(frame.buffer);

    expect(ctx.started.length).toBe(2);
    // Second chunk starts exactly one chunk-duration later — gapless.
    expect(ctx.started[1].startedAt.when).toBeCloseTo(2400 / 24000, 5);
    player.fail();
  });
});

describe('peaksFromChunkList', () => {
  it('returns normalized peaks across chunk boundaries', () => {
    const quiet = new Float32Array(1000).fill(0.1);
    const loud = new Float32Array(1000).fill(0.8);
    const peaks = peaksFromChunkList([quiet, loud], 10);
    expect(peaks.length).toBe(10);
    expect(Math.max(...peaks)).toBe(1); // normalized
    expect(peaks[0]).toBeLessThan(peaks[9]); // loud tail dominates
  });
  it('handles empty input', () => {
    expect(peaksFromChunkList([])).toBeNull();
  });
});

describe('supportsStreamingPreview', () => {
  it('is true with an AudioContext and false without', () => {
    expect(supportsStreamingPreview()).toBe(true);
    const saved = window.AudioContext;
    delete window.AudioContext;
    delete window.webkitAudioContext;
    expect(supportsStreamingPreview()).toBe(false);
    window.AudioContext = saved;
  });
});

// ── resolveRemoteTtsTarget ──────────────────────────────────────────────────
//
// The stream is rendered by THIS process, so a progressive preview on a
// remote target would quietly run the job on this machine after the user
// picked their 4090. This is the check that stops that, and it must fail
// open: a picker that cannot be reached must never block a local render.

describe('resolveRemoteTtsTarget', () => {
  const json = (body, ok = true) => ({ ok, json: async () => body });

  it('asks routing for the tts operation specifically', async () => {
    apiFetch.mockResolvedValue(json({ active: { remote: false } }));
    await resolveRemoteTtsTarget();
    expect(apiFetch.mock.calls[0][0]).toBe('/workers/target?op=tts');
    // A dead backend must fail this probe in one round trip, not stall the
    // click behind the transport retry ladder.
    expect(apiFetch.mock.calls[0][1]).toMatchObject({ retryTransport: false });
  });

  it('reports the worker when the resolved target is remote', async () => {
    apiFetch.mockResolvedValue(
      json({ active: { remote: true, worker_id: 'w1', label: 'desktop-4090' } }),
    );
    expect(await resolveRemoteTtsTarget()).toEqual({ workerId: 'w1', label: 'desktop-4090' });
  });

  it('answers local for a fallback decision, so streaming stays available', async () => {
    apiFetch.mockResolvedValue(
      json({ active: { remote: false, reason: 'desktop-4090 is offline — running locally' } }),
    );
    expect(await resolveRemoteTtsTarget()).toBeNull();
  });

  it('answers local when the endpoint errors or the backend is unreachable', async () => {
    apiFetch.mockResolvedValue(json({ detail: 'nope' }, false));
    expect(await resolveRemoteTtsTarget()).toBeNull();

    apiFetch.mockRejectedValue(new Error('connection refused'));
    expect(await resolveRemoteTtsTarget()).toBeNull();
  });
});

// ── streamGenerateSpeech ────────────────────────────────────────────────────

describe('streamGenerateSpeech', () => {
  it('plays chunks progressively, flips the label on done, resolves metadata', async () => {
    apiFetch.mockResolvedValue(
      ndjsonResponse([startEvent(3), chunkEvent(0), chunkEvent(1), chunkEvent(2), doneEvent], {
        'X-Seed': '7',
      }),
    );
    const onHeaders = vi.fn();
    const onProgress = vi.fn();

    const meta = await streamGenerateSpeech(new FormData(), {
      label: 'Streaming preview…',
      finalLabel: 'Generated audio',
      onHeaders,
      onProgress,
    });

    expect(meta.id).toBe('abc12345');
    expect(meta.audio_path).toBe('abc12345.wav');
    expect(onHeaders).toHaveBeenCalledTimes(1);
    expect(onProgress).toHaveBeenLastCalledWith(100);

    // All three chunks were scheduled on one context.
    const ctx = FakeAudioContext.instances[0];
    expect(ctx.started.length).toBe(3);

    // Tracked 'output' claim → mini-player bar; label flipped at completion;
    // duration covers the buffered chunks (3 × 100 ms minus 2 × 50 ms fades).
    const track = getPlaybackTrack();
    expect(track.source).toBe('output');
    expect(track.label).toBe('Generated audio');
    expect(track.duration).toBeCloseTo(0.2, 3);
    expect(track.peaks?.length).toBeGreaterThan(0);
    expect(track.canSeek).toBe(true);
  });

  it('appends stream=true without mutating the caller FormData', async () => {
    apiFetch.mockResolvedValue(ndjsonResponse([startEvent(1), chunkEvent(0), doneEvent]));
    const fd = new FormData();
    fd.append('text', 'hello');
    await streamGenerateSpeech(fd, {});
    expect(fd.get('stream')).toBeNull(); // caller's copy untouched
    const sent = apiFetch.mock.calls[0][1].body;
    expect(sent.get('stream')).toBe('true');
    expect(sent.get('text')).toBe('hello');
  });

  it('turns an in-band error event into StreamingPreviewError and releases the bar', async () => {
    apiFetch.mockResolvedValue(
      ndjsonResponse([startEvent(3), chunkEvent(0), { type: 'error', detail: 'engine boom' }]),
    );
    await expect(streamGenerateSpeech(new FormData(), {})).rejects.toThrow(StreamingPreviewError);
    expect(getPlaybackTrack()).toBeNull(); // playback torn down for the fallback
    expect(FakeAudioContext.instances[0].state).toBe('closed');
  });

  it('carries the backend error class from the error frame (#1800)', async () => {
    // Every unclassified engine failure renders the same floor message, so
    // the auto-filed reports were byte-identical and none could be triaged.
    // The class name is the only thing that separates them.
    apiFetch.mockResolvedValue(
      ndjsonResponse([
        startEvent(3),
        chunkEvent(0),
        { type: 'error', detail: 'Generation failed.', error_class: 'MemoryError' },
      ]),
    );
    await expect(streamGenerateSpeech(new FormData(), {})).rejects.toMatchObject({
      errorClass: 'MemoryError',
    });
  });

  it('leaves the error class null when the frame omits it', async () => {
    apiFetch.mockResolvedValue(
      ndjsonResponse([startEvent(3), chunkEvent(0), { type: 'error', detail: 'boom' }]),
    );
    await expect(streamGenerateSpeech(new FormData(), {})).rejects.toMatchObject({
      errorClass: null,
    });
  });

  it('carries the retryable marker from a GPU-timeout error frame (#1190)', async () => {
    // A retryable failure means the backend already spent the full budget on
    // this text and the abandoned job still holds the device — useTTS uses
    // this flag to skip the classic re-render instead of paying the timeout
    // a second time.
    apiFetch.mockResolvedValue(
      ndjsonResponse([
        startEvent(3),
        chunkEvent(0),
        { type: 'error', detail: 'ran for more than 300s', retryable: true, retry_after: 45 },
      ]),
    );
    const err = await streamGenerateSpeech(new FormData(), {}).catch((e) => e);
    expect(err).toBeInstanceOf(StreamingPreviewError);
    expect(err.retryable).toBe(true);
    expect(err.retryAfter).toBe(45);

    // A plain engine failure stays non-retryable → the classic fallback still
    // applies, unchanged.
    apiFetch.mockResolvedValue(
      ndjsonResponse([startEvent(2), chunkEvent(0), { type: 'error', detail: 'engine boom' }]),
    );
    const plain = await streamGenerateSpeech(new FormData(), {}).catch((e) => e);
    expect(plain.retryable).toBe(false);
  });

  it.each([
    ['GPU_ARCH_UNSUPPORTED', 'gpu_arch_unsupported'],
    ['WINDOWS_APP_CONTROL_BLOCKED', 'windows_app_control_blocked'],
    ['AUDIO_IO_FAILED', 'audio_io_failed'],
  ])('localizes %s while retaining retry metadata', async (topic, key) => {
    const translate = vi.spyOn(i18n, 't').mockReturnValue('Localized recovery');
    const terminal = topic !== 'AUDIO_IO_FAILED';
    try {
      apiFetch.mockResolvedValue(
        ndjsonResponse([
          {
            type: 'error',
            docs_topic: topic,
            detail: 'English fallback',
            terminal,
            retryable: !terminal,
          },
        ]),
      );
      const error = await streamGenerateSpeech(new FormData(), {}).catch((e) => e);
      expect(error.message).toBe('Localized recovery');
      expect(translate).toHaveBeenCalledWith(`tts_errors.${key}`);
      expect(error.terminal).toBe(terminal);
      expect(shouldFallbackToClassic(error)).toBe(false);
    } finally {
      translate.mockRestore();
    }
  });

  it('localizes a terminal profile language refusal without retrying', async () => {
    const translate = vi.spyOn(i18n, 't').mockReturnValue('Localized profile guidance');
    try {
      apiFetch.mockResolvedValue(
        ndjsonResponse([
          {
            type: 'error',
            code: 'profile_language_rejected',
            language: 'Persian',
            detail: 'English fallback',
            retryable: false,
            terminal: true,
          },
        ]),
      );
      const error = await streamGenerateSpeech(new FormData(), {}).catch((e) => e);
      expect(error.message).toBe('Localized profile guidance');
      expect(shouldFallbackToClassic(error)).toBe(false);
      expect(translate).toHaveBeenCalledWith('tts_errors.profile_language_rejected', {
        language: 'Persian',
      });
    } finally {
      translate.mockRestore();
    }
  });

  it('marks actionable clone-reference errors terminal to prevent a classic retry', async () => {
    apiFetch.mockResolvedValue(
      ndjsonResponse([
        startEvent(1),
        { type: 'error', detail: '[clone_ref_no_speech] trim the reference' },
      ]),
    );

    const err = await streamGenerateSpeech(new FormData(), {}).catch((e) => e);

    expect(err).toBeInstanceOf(StreamingPreviewError);
    expect(err.terminal).toBe(true);
    expect(shouldFallbackToClassic(err)).toBe(false);
  });

  it('rejects with StreamingPreviewError when the stream ends without done', async () => {
    apiFetch.mockResolvedValue(ndjsonResponse([startEvent(2), chunkEvent(0)]));
    await expect(streamGenerateSpeech(new FormData(), {})).rejects.toThrow(StreamingPreviewError);
  });

  it('wraps a mid-body transport drop but lets pre-stream ApiError through untouched', async () => {
    // Mid-body drop → StreamingPreviewError (fallback signal).
    const enc = new TextEncoder();
    apiFetch.mockResolvedValue({
      headers: new Headers(),
      body: new ReadableStream({
        start(c) {
          c.enqueue(enc.encode(JSON.stringify(startEvent(2)) + '\n'));
          c.error(new TypeError('network dropped'));
        },
      }),
    });
    await expect(streamGenerateSpeech(new FormData(), {})).rejects.toThrow(StreamingPreviewError);

    // Pre-stream HTTP failure → original error identity (NO fallback; the
    // classic flow would fail identically).
    const apiErr = Object.assign(new Error('400 Bad Request: nope'), { name: 'ApiError' });
    apiFetch.mockRejectedValue(apiErr);
    await expect(streamGenerateSpeech(new FormData(), {})).rejects.toBe(apiErr);
  });

  // #1330 — the take is real, but part of the text produced no audio. Driven
  // through the actual NDJSON reader rather than by matching source text
  // (CodeRabbit): a serialization the client cannot parse would pass the
  // literal check and still leave the user with a silently short take.
  it('forwards a warning frame to onWarning and still resolves with done', async () => {
    const warned = [];
    apiFetch.mockResolvedValue(
      ndjsonResponse([
        startEvent(3),
        chunkEvent(0),
        chunkEvent(1),
        { type: 'warning', code: 'dropped_chunks', count: 1, text: ['the tail that vanished.'] },
        doneEvent,
      ]),
    );
    const meta = await streamGenerateSpeech(new FormData(), {
      onWarning: (ev) => warned.push(ev),
    });
    // Not an error: the stream completed and the caller got its metadata.
    expect(meta.id).toBe('abc12345');
    expect(warned).toHaveLength(1);
    expect(warned[0]).toMatchObject({ code: 'dropped_chunks', count: 1 });
    expect(warned[0].text).toEqual(['the tail that vanished.']);
  });

  it('does not require the caller to handle warnings', async () => {
    // A consumer that never passes onWarning must not crash on the new frame.
    // total_chunks covers the chunk delivered plus the two that dropped —
    // a payload the backend could actually emit (CodeRabbit).
    apiFetch.mockResolvedValue(
      ndjsonResponse([
        startEvent(3),
        chunkEvent(0),
        { type: 'warning', code: 'dropped_chunks', count: 2, text: [] },
        doneEvent,
      ]),
    );
    await expect(streamGenerateSpeech(new FormData(), {})).resolves.toMatchObject({
      id: 'abc12345',
    });
  });
});

// ── createStreamingChunkPlayer transport ────────────────────────────────────

describe('createStreamingChunkPlayer', () => {
  it('supports seek within the buffered region (reschedules from the target)', () => {
    const player = createStreamingChunkPlayer({
      label: 'x',
      sampleRate: 24000,
      crossfadeMs: 0,
    });
    player.appendPcm16Base64(chunkEvent(0).pcm);
    player.appendPcm16Base64(chunkEvent(1).pcm);
    const ctx = FakeAudioContext.instances[0];
    const before = ctx.started.length;
    seekActivePlayback(0.15); // inside chunk 1 (0.1–0.2 s timeline)
    expect(ctx.started.length).toBeGreaterThan(before); // rescheduled
    const reseek = ctx.started[ctx.started.length - 1];
    expect(reseek.startedAt.offset).toBeCloseTo(0.05, 3); // intra-chunk offset
    expect(getPlaybackTrack().currentTime).toBeCloseTo(0.15, 3);
    player.fail();
  });

  it('stop via the manager finishes the player and closes the context', () => {
    const onDone = vi.fn();
    const player = createStreamingChunkPlayer({
      label: 'x',
      sampleRate: 24000,
      crossfadeMs: 50,
      onDone,
    });
    player.appendPcm16Base64(chunkEvent(0).pcm);
    stopActivePlayback();
    expect(onDone).toHaveBeenCalledWith('stopped');
    expect(player.stopped).toBe(true);
    expect(FakeAudioContext.instances[0].state).toBe('closed');
  });
});
