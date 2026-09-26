/**
 * Streaming synthesis must go through the same door as every other synth.
 *
 * `streamGenerateSpeech` used to POST /generate via `apiFetch` directly — a
 * second, parallel entrance. Everything bolted onto "the one call every synth
 * path shares" therefore did not apply to it: the in-flight count that stops
 * the updater relaunching mid-synthesis, and the under-provisioned-hardware
 * preflight (Greptile P1, #1288). A chokepoint with two doors is not a
 * chokepoint, and the second door is invisible until someone on a 4 GB card
 * uses the streaming path and gets no warning.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { apiFetch, preflight, state } = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  preflight: vi.fn(),
  state: { ttsInflight: 0, max: 0 },
}));

vi.mock('../api/client', () => ({
  API: '',
  apiUrl: (p) => p,
  apiFetch: (...a) => apiFetch(...a),
  apiJson: vi.fn(),
}));

vi.mock('../store', () => ({
  useAppStore: {
    getState: () => ({
      ...state,
      addTtsInflight: (d) => {
        state.ttsInflight = Math.max(0, state.ttsInflight + d);
        state.max = Math.max(state.max, state.ttsInflight);
      },
    }),
  },
}));

vi.mock('../utils/generatePreflight', () => ({
  warnIfEngineUnderProvisioned: (...a) => preflight(...a),
  onEngineSelected: vi.fn(),
}));

vi.mock('../utils/playback', () => ({ claimTrackedPlayback: vi.fn() }));

import { streamGenerateSpeech } from '../utils/streamingTts';

describe('streaming synthesis shares the generate chokepoint', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    preflight.mockReset();
    state.ttsInflight = 0;
    state.max = 0;
  });

  it('runs the under-provisioned preflight before streaming', async () => {
    // Fail the stream immediately — this test is about what happens BEFORE the
    // first byte, and the streaming machinery past that point is covered by
    // streamingTts.test.js.
    apiFetch.mockResolvedValue({ body: null, headers: new Map() });

    await streamGenerateSpeech(new FormData(), {}).catch(() => {});

    expect(preflight).toHaveBeenCalledTimes(1);
  });

  it('counts the streaming synth as in flight', async () => {
    apiFetch.mockResolvedValue({ body: null, headers: new Map() });

    await streamGenerateSpeech(new FormData(), {}).catch(() => {});

    expect(state.max).toBeGreaterThanOrEqual(1);
  });

  it('still posts to /generate with the stream flag', async () => {
    apiFetch.mockResolvedValue({ body: null, headers: new Map() });

    await streamGenerateSpeech(new FormData(), {}).catch(() => {});

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, opts] = apiFetch.mock.calls[0];
    expect(path).toBe('/generate');
    expect(opts.method).toBe('POST');
    expect(opts.body.get('stream')).toBe('true');
  });
});

describe('in-flight count spans the whole stream', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    preflight.mockReset();
    state.ttsInflight = 0;
    state.max = 0;
  });

  // Greptile P1: generateSpeech releases its claim when the Response resolves
  // (headers), but the audio is generated while the BODY is read. The updater
  // would see "idle" for the entire synthesis and could relaunch mid-stream.
  it('is still in flight while the body is being consumed', async () => {
    const seen = [];
    apiFetch.mockResolvedValue({
      headers: new Map(),
      body: {
        getReader: () => ({
          read: async () => {
            seen.push(state.ttsInflight);
            return { done: true, value: undefined };
          },
        }),
      },
    });

    await streamGenerateSpeech(new FormData(), {}).catch(() => {});

    expect(seen.length).toBeGreaterThan(0);
    expect(Math.min(...seen)).toBeGreaterThan(0);
  });

  it('releases the claim once the stream finishes', async () => {
    apiFetch.mockResolvedValue({
      headers: new Map(),
      body: { getReader: () => ({ read: async () => ({ done: true }) }) },
    });

    await streamGenerateSpeech(new FormData(), {}).catch(() => {});
    expect(state.ttsInflight).toBe(0);
  });
});
