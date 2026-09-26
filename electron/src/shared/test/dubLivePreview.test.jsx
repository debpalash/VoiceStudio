/**
 * Live dub preview (ROADMAP: "Real-time dub preview (stream TTS as you edit)").
 *
 * The contract under test:
 *   • an edit streams over /ws/tts only after the ~400 ms debounce, with the
 *     segment's CAST voice resolved through segmentGenInputs;
 *   • a new keystroke or a segment switch closes the previous socket — no
 *     socket pile-up;
 *   • every stream runs inside the process-wide withTtsInflight admission the
 *     /generate and Convert paths share (busy → localized toast, no socket);
 *   • a segment with no CAST voice never opens a socket and gets an
 *     actionable "assign a voice" toast;
 *   • binary PCM frames reach the chunk player; `done` finalizes it; abort
 *     silences it. Nothing here persists job audio.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const { mocks } = vi.hoisted(() => {
  const players = [];
  return {
    mocks: {
      state: { ttsInflight: 0, dubLang: 'Auto' },
      authenticatedWsUrl: vi.fn(async () => 'ws://test/ws/tts?ws_ticket=one-use'),
      players,
      createStreamingChunkPlayer: vi.fn((options = {}) => {
        const player = {
          appendPcm16Base64: vi.fn(),
          appendPcm16Bytes: vi.fn(),
          finalize: vi.fn(),
          fail: vi.fn(),
          onDone: options.onDone,
        };
        players.push(player);
        return player;
      }),
      toast: Object.assign(vi.fn(), { error: vi.fn() }),
    },
  };
});

vi.mock('../api/client', () => ({
  API: 'http://test',
  apiUrl: (p) => p,
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
}));
vi.mock('../api/authSession', () => ({ authenticatedWsUrl: mocks.authenticatedWsUrl }));
vi.mock('../utils/generatePreflight', () => ({ warnIfEngineUnderProvisioned: vi.fn() }));
vi.mock('../utils/streamingTts', () => ({
  createStreamingChunkPlayer: mocks.createStreamingChunkPlayer,
  supportsStreamingPreview: () => true,
}));
vi.mock('react-hot-toast', () => ({ toast: mocks.toast }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k) => k }) }));
vi.mock('../store', () => ({
  useAppStore: {
    getState: () => ({
      ...mocks.state,
      addTtsInflight: (d) => {
        mocks.state.ttsInflight = Math.max(0, mocks.state.ttsInflight + d);
      },
    }),
  },
}));

import useDubLivePreview, { LIVE_PREVIEW_DEBOUNCE_MS } from '../hooks/useDubLivePreview';

class FakeWebSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.closed = false;
    this.binaryType = 'blob';
    FakeWebSocket.instances.push(this);
  }
  send(data) {
    this.sent.push(data);
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
}

const SEG = { id: 'seg-1', text: 'Hola mundo', profile_id: 'prof-1', target_lang: 'es' };

const renderPreview = (enabled = true) =>
  renderHook(({ on }) => useDubLivePreview({ enabled: on }), { initialProps: { on: enabled } });

/** Type into a segment, run the debounce, and flush the async connect. */
const editAndSettle = async (result, seg, text) => {
  act(() => result.current.onLiveEdit(seg, text));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_PREVIEW_DEBOUNCE_MS);
  });
};

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  mocks.players.length = 0;
  mocks.state.ttsInflight = 0;
  mocks.state.dubLang = 'Auto';
  mocks.authenticatedWsUrl.mockClear();
  mocks.createStreamingChunkPlayer.mockClear();
  mocks.toast.mockClear();
  mocks.toast.error.mockClear();
  vi.stubGlobal('WebSocket', FakeWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('useDubLivePreview', () => {
  it('streams only after the debounce, with the CAST voice and one-use ticket', async () => {
    const { result } = renderPreview();

    act(() => result.current.onLiveEdit(SEG, 'Hola mundo'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_PREVIEW_DEBOUNCE_MS - 1);
    });
    expect(FakeWebSocket.instances).toHaveLength(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(mocks.authenticatedWsUrl).toHaveBeenCalledWith('/ws/tts', { apiBase: 'http://test' });
    expect(FakeWebSocket.instances).toHaveLength(1);

    const ws = FakeWebSocket.instances[0];
    expect(ws.url).toContain('ws_ticket=one-use');
    expect(ws.binaryType).toBe('arraybuffer');
    act(() => ws.open());
    expect(JSON.parse(ws.sent[0])).toEqual({
      text: 'Hola mundo',
      voice: 'prof-1',
      speed: 1.0,
      language: 'es',
    });
  });

  it('plays binary PCM frames through the chunk player and finalizes on done', async () => {
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'Hola mundo');
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    expect(mocks.state.ttsInflight).toBe(1); // admission held while streaming

    act(() => ws.onmessage({ data: JSON.stringify({ type: 'start', sample_rate: 24000 }) }));
    expect(mocks.createStreamingChunkPlayer).toHaveBeenCalledWith(
      expect.objectContaining({ sampleRate: 24000 }),
    );

    const frame = new Int16Array([100, -100, 500]).buffer;
    act(() => ws.onmessage({ data: frame }));
    expect(mocks.players[0].appendPcm16Bytes).toHaveBeenCalledWith(frame);

    await act(async () => {
      ws.onmessage({ data: JSON.stringify({ type: 'done', duration_s: 1.2 }) });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.players[0].finalize).toHaveBeenCalled();
    // done ≠ abort: the buffered tail keeps playing, it is not torn down.
    expect(mocks.players[0].fail).not.toHaveBeenCalled();
    expect(mocks.state.ttsInflight).toBe(0); // admission released
    expect(result.current.liveSegId).toBe(null);
  });

  it('can still silence a finalized buffered tail when a newer edit arrives', async () => {
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'Old line');
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.onmessage({ data: JSON.stringify({ type: 'start', sample_rate: 24000 }) }));
    await act(async () => {
      ws.onmessage({ data: JSON.stringify({ type: 'done' }) });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.players[0].finalize).toHaveBeenCalled();
    expect(mocks.players[0].fail).not.toHaveBeenCalled();

    act(() => result.current.onLiveEdit(SEG, 'New line'));
    expect(mocks.players[0].fail).toHaveBeenCalledOnce();
  });

  it('closes the previous socket on the next keystroke', async () => {
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'Hola mun');
    const first = FakeWebSocket.instances[0];
    act(() => first.open());
    act(() => first.onmessage({ data: JSON.stringify({ type: 'start', sample_rate: 24000 }) }));

    await editAndSettle(result, SEG, 'Hola mundo');
    expect(first.closed).toBe(true);
    expect(mocks.players[0].fail).toHaveBeenCalled(); // the stale audio is silenced
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(FakeWebSocket.instances[1].closed).toBe(false);
    expect(mocks.state.ttsInflight).toBe(1); // exactly one admission slot held
  });

  it('closes the previous socket when the user moves to another segment', async () => {
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'Hola mundo');
    const first = FakeWebSocket.instances[0];
    act(() => first.open());

    const other = { id: 'seg-2', text: 'Adiós', profile_id: 'prof-2' };
    await editAndSettle(result, other, 'Adiós amigo');
    expect(first.closed).toBe(true);
    const second = FakeWebSocket.instances[1];
    act(() => second.open());
    expect(JSON.parse(second.sent[0]).voice).toBe('prof-2');
    expect(result.current.liveSegId).toBe('seg-2');
  });

  it('does not start an older intent when teardown synchronously queues a newer edit', async () => {
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'First line');
    const first = FakeWebSocket.instances[0];
    act(() => first.open());
    act(() => first.onmessage({ data: JSON.stringify({ type: 'start', sample_rate: 24000 }) }));

    const older = { id: 'seg-2', text: 'Older', profile_id: 'prof-2' };
    const latest = { id: 'seg-3', text: 'Latest', profile_id: 'prof-3' };
    mocks.players[0].fail.mockImplementationOnce(() => {
      result.current.onLiveEdit(latest, latest.text);
    });
    await act(async () => {
      result.current.onLiveToggle(older);
      await vi.advanceTimersByTimeAsync(0);
    });

    // The older toggle was superseded while it awaited admission teardown.
    // It must not open a socket during the latest edit's debounce window.
    expect(FakeWebSocket.instances).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_PREVIEW_DEBOUNCE_MS);
    });
    expect(FakeWebSocket.instances).toHaveLength(2);
    const latestSocket = FakeWebSocket.instances[1];
    act(() => latestSocket.open());
    expect(JSON.parse(latestSocket.sent[0])).toEqual(
      expect.objectContaining({ text: 'Latest', voice: 'prof-3' }),
    );
  });

  it('skips segments with no CAST voice and says how to fix it', async () => {
    const { result } = renderPreview();
    await editAndSettle(result, { id: 'seg-3', text: 'Sin voz' }, 'Sin voz');
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(mocks.toast).toHaveBeenCalledWith('dub.live_preview_pick_voice', expect.anything());
  });

  it('expands legacy preset: voices to instruct text instead of skipping them', async () => {
    const { result } = renderPreview();
    const seg = { id: 'seg-4', text: 'Preset line', profile_id: 'preset:narrator' };
    await editAndSettle(result, seg, 'Preset line');
    // Same segmentGenInputs expansion the dub generate body uses: the request
    // must carry instruct text, never a profile id the backend can't resolve.
    expect(FakeWebSocket.instances).toHaveLength(1);
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    const payload = JSON.parse(ws.sent[0]);
    expect(payload.voice).toBeUndefined();
    expect(payload.instruct).toContain('male');
  });

  it('respects the process-wide TTS admission: busy → toast, no socket', async () => {
    mocks.state.ttsInflight = 1; // a /generate or Convert is running
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'Hola mundo');
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(mocks.toast).toHaveBeenCalledWith(
      'tts_errors.generation_in_progress',
      expect.anything(),
    );
    expect(mocks.state.ttsInflight).toBe(1); // untouched — we never admitted
    expect(result.current.liveSegId).toBe(null);
  });

  it('does nothing while the pref is off, and stops the stream when toggled off', async () => {
    const { result, rerender } = renderPreview(false);
    await editAndSettle(result, SEG, 'Hola mundo');
    expect(FakeWebSocket.instances).toHaveLength(0);

    rerender({ on: true });
    await editAndSettle(result, SEG, 'Hola mundo');
    expect(FakeWebSocket.instances).toHaveLength(1);
    act(() => FakeWebSocket.instances[0].open());

    await act(async () => {
      rerender({ on: false });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(FakeWebSocket.instances[0].closed).toBe(true);
    expect(mocks.state.ttsInflight).toBe(0);
  });

  it('surfaces backend error frames and releases admission', async () => {
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'Hola mundo');
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    await act(async () => {
      ws.onmessage({ data: JSON.stringify({ type: 'error', detail: 'engine cannot run' }) });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.toast.error).toHaveBeenCalledWith('tts_errors.error_prefix');
    expect(ws.closed).toBe(true);
    expect(mocks.state.ttsInflight).toBe(0);
  });

  it('tells the user and releases admission when the ticket handshake fails', async () => {
    // A backend whose ticket allowlist lacks /ws/tts answers 422; that must
    // not look like "the toggle does nothing" (the #1769 first-cut symptom).
    mocks.authenticatedWsUrl.mockRejectedValueOnce(new Error('ticket refused'));
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'Hola mundo');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(mocks.toast.error).toHaveBeenCalledWith('tts_errors.error_prefix');
    expect(mocks.state.ttsInflight).toBe(0);
    expect(result.current.liveSegId).toBe(null);
  });

  it('releases admission when WebSocket construction throws synchronously', async () => {
    vi.stubGlobal(
      'WebSocket',
      class ThrowingWebSocket {
        constructor() {
          throw new Error('constructor failed');
        }
      },
    );
    const { result } = renderPreview();
    await editAndSettle(result, SEG, 'Hola mundo');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.state.ttsInflight).toBe(0);
    expect(result.current.liveSegId).toBe(null);
  });

  it('row speaker toggle streams the current text immediately and stops on second press', async () => {
    const { result } = renderPreview();
    await act(async () => {
      result.current.onLiveToggle(SEG);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(result.current.liveSegId).toBe('seg-1');

    await act(async () => {
      result.current.onLiveToggle(SEG);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(FakeWebSocket.instances[0].closed).toBe(true);
    expect(result.current.liveSegId).toBe(null);
  });

  it('cleans up the socket on unmount', async () => {
    const { result, unmount } = renderPreview();
    await editAndSettle(result, SEG, 'Hola mundo');
    act(() => FakeWebSocket.instances[0].open());
    unmount();
    expect(FakeWebSocket.instances[0].closed).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.state.ttsInflight).toBe(0);
  });
});
