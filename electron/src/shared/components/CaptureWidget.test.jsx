/**
 * CaptureWidget pill behaviour — mocked WS + Tauri invoke.
 *
 * Covers the truthfulness rebuild: model status frames render real
 * download/load progress, "Inserted" only appears after native delivery resolves
 * Ok, an "a11y:"-prefixed paste failure renders the actionable Accessibility
 * error, Esc aborts without pasting, live retract-retype is opt-in (default
 * sessions never call simulate_type), the missing-Accessibility setup state
 * shows on mount, and the waveform bars move from real mic frames.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

const captureLocales = import.meta.glob('../i18n/locales/*.json', {
  eager: true,
  import: 'default',
});

// ── Hoisted mock state (vi.mock factories may only reference vi.hoisted vars) ──
const mocks = vi.hoisted(() => {
  const state = {
    dictationEnabled: true,
    dictationMode: 'toggle',
    dictationModelId: 'sherpa-parakeet-tdt-v3', // sherpa → raw-PCM live path
    aecEnabled: false,
    loadDictationPrefs: () => {},
  };
  const holder = {
    // Per-test knobs for the Tauri invoke mock.
    a11y: true,
    paste: async () => 'inserted',
    copy: async () => 'copied',
    type: async () => 'inserted',
    activate: async () => undefined,
    reject: async () => undefined,
    finish: async () => undefined,
    calls: [],
    handlers: {},
    // Captured micCapture frame callback (the worklet feed).
    onFrame: null,
    startMic: async (_stream, onFrame) => {
      holder.onFrame = onFrame;
      return async () => {};
    },
    // Stable spy for getCurrentWindow().hide — a fresh vi.fn() per call would
    // make the native hide unassertable.
    hideWindow: vi.fn(async () => {}),
  };
  return {
    state,
    holder,
    authenticatedWsUrl: vi.fn(async (path) => `ws://test${path}&ws_ticket=one-use`),
    apiFetch: vi.fn(async () => ({ json: async () => ({}) })),
    copyText: vi.fn(async () => {}),
    invoke: async (cmd, args) => {
      holder.calls.push([cmd, args]);
      if (cmd === 'begin_dictation_capture_registration') {
        return holder.calls.filter(([command]) => command === cmd).length;
      }
      if (cmd === 'check_accessibility') return holder.a11y;
      if (cmd === 'simulate_paste') return holder.paste(cmd, args);
      if (cmd === 'copy_dictation_output_session') return holder.copy(cmd, args);
      if (cmd === 'simulate_type') return holder.type(cmd, args);
      if (cmd === 'activate_dictation_output_session') return holder.activate(cmd, args);
      if (cmd === 'reject_dictation_output_session') return holder.reject(cmd, args);
      if (cmd === 'finish_dictation_output_session') return holder.finish(cmd, args);
      return undefined;
    },
  };
});

vi.mock('../store', () => ({
  useAppStore: Object.assign((sel) => sel(mocks.state), { getState: () => mocks.state }),
}));
vi.mock('../api/client', () => ({
  API: 'http://test',
  wsUrl: (p) => `ws://test${p}`,
  apiFetch: mocks.apiFetch,
}));
vi.mock('../api/authSession', () => ({ authenticatedWsUrl: mocks.authenticatedWsUrl }));
vi.mock('../pages/Transcriptions', () => ({ addTranscription: vi.fn() }));
vi.mock('../utils/copyText', () => ({ copyText: mocks.copyText }));
vi.mock('react-hot-toast', () => ({ toast: { error: vi.fn() } }));
vi.mock('@tauri-apps/api/core', () => ({ invoke: mocks.invoke }));
vi.mock('@tauri-apps/api/event', () => ({
  emit: vi.fn(async () => {}),
  listen: vi.fn(async (name, handler) => {
    mocks.holder.handlers[name] = handler;
    return () => delete mocks.holder.handlers[name];
  }),
}));
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({ hide: mocks.holder.hideWindow }),
}));
vi.mock('../utils/aec/micCapture', () => ({
  startMicCapture: (...args) => mocks.holder.startMic(...args),
}));

import CaptureWidget from './CaptureWidget';

// ── Browser API fakes (jsdom has neither WebSocket use here nor MediaRecorder) ──
class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN; // pretend the connect is instant
    this.sent = [];
    this._listeners = {};
    FakeWebSocket.instances.push(this);
  }
  addEventListener(type, fn) {
    (this._listeners[type] ||= []).push(fn);
  }
  send(d) {
    this.sent.push(d);
  }
  close() {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
  /** Deliver a backend JSON frame. */
  msg(obj) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  msgAsync(obj) {
    return this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

class FakeMediaRecorder {
  static isTypeSupported() {
    return true;
  }
  constructor() {
    this.state = 'inactive';
  }
  start() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
  }
}

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

// Start through the native event that carries Rust's captured output target.
async function startSession() {
  return startNativeSession();
}

async function startNativeSession(sessionId = 'capture-session-1') {
  await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));
  await act(async () => {
    await mocks.holder.handlers['tray-dictate']({ payload: { sessionId } });
  });
  await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
  await screen.findByText(/Listening/);
  return FakeWebSocket.instances[0];
}

describe('CaptureWidget', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
    mocks.holder.a11y = true;
    mocks.holder.paste = async () => 'inserted';
    mocks.holder.copy = async () => 'copied';
    mocks.holder.type = async () => 'inserted';
    mocks.holder.activate = async () => undefined;
    mocks.holder.reject = async () => undefined;
    mocks.holder.finish = async () => undefined;
    mocks.holder.calls = [];
    mocks.holder.handlers = {};
    mocks.holder.onFrame = null;
    mocks.holder.startMic = async (_stream, onFrame) => {
      mocks.holder.onFrame = onFrame;
      return async () => {};
    };
    mocks.holder.hideWindow.mockClear();
    mocks.copyText.mockReset();
    mocks.copyText.mockResolvedValue(undefined);
    mocks.apiFetch.mockReset();
    mocks.apiFetch.mockImplementation(async () => ({ json: async () => ({}) }));
    mocks.authenticatedWsUrl.mockClear();
    mocks.authenticatedWsUrl.mockImplementation(
      async (path) => `ws://test${path}${path.includes('?') ? '&' : '?'}ws_ticket=one-use`,
    );
    mocks.state.dictationMode = 'toggle';
    mocks.state.dictationEnabled = true;
    mocks.state.dictationModelId = 'sherpa-parakeet-tdt-v3';
    mocks.state.loadDictationPrefs = async () => {};
    FakeWebSocket.instances = [];
    global.WebSocket = FakeWebSocket;
    global.MediaRecorder = FakeMediaRecorder;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) },
    });
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete window.__TAURI_INTERNALS__;
    delete global.WebSocket;
    delete global.MediaRecorder;
  });

  const pasteCalls = () => mocks.holder.calls.filter(([c]) => c === 'simulate_paste');
  const copyCalls = () => mocks.holder.calls.filter(([c]) => c === 'copy_dictation_output_session');
  const typeCalls = () => mocks.holder.calls.filter(([c]) => c === 'simulate_type');

  it('provides a translated Inserted outcome in every supported locale', () => {
    expect(Object.keys(captureLocales)).toHaveLength(21);
    for (const [path, locale] of Object.entries(captureLocales)) {
      expect(locale.capture.inserted, path).toBeTruthy();
      if (!path.endsWith('/en.json')) expect(locale.capture.inserted, path).not.toBe('Inserted');
    }
  });

  it('attaches the native listener but waits for persisted prefs before ready and start', async () => {
    let resolveHydration;
    mocks.state.loadDictationPrefs = () =>
      new Promise((resolve) => {
        resolveHydration = () => {
          mocks.state.dictationMode = 'hold';
          mocks.state.dictationModelId = 'sherpa-whisper-tiny';
          resolve();
        };
      });
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop() {} }] }));
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    render(withI18n(<CaptureWidget />));
    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));
    expect(mocks.holder.calls.some(([command]) => command === 'mark_dictation_capture_ready')).toBe(
      false,
    );

    let earlyStart;
    act(() => {
      earlyStart = mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'hydrated-session' },
      });
    });
    await act(async () => Promise.resolve());
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(mocks.holder.calls).not.toContainEqual([
      'activate_dictation_output_session',
      { sessionId: 'hydrated-session' },
    ]);

    await act(async () => {
      resolveHydration();
      await earlyStart;
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    await screen.findByText(/Listening/);
    expect(FakeWebSocket.instances[0].url).toContain('model=sherpa-whisper-tiny');
    expect(mocks.holder.calls.some(([command]) => command === 'mark_dictation_capture_ready')).toBe(
      true,
    );

    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate-stop']();
    });
    await screen.findByText(/Transcribing/);
  });

  it('marks native capture ready with safe seeds when prefs hydration fails', async () => {
    mocks.state.loadDictationPrefs = async () => {
      throw new Error('prefs backend unavailable');
    };
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(withI18n(<CaptureWidget />));

    await waitFor(() =>
      expect(
        mocks.holder.calls.some(([command]) => command === 'mark_dictation_capture_ready'),
      ).toBe(true),
    );
    const ws = await startNativeSession('seed-session');
    expect(ws.url).toContain('model=sherpa-parakeet-tdt-v3');
  });

  // The widget is its own window with its own store, created before the
  // backend is listening. A failed first load used to be memoized, pinning the
  // seed model for the life of the app: the main window checked the model the
  // user picked and said "ready", while the pill asked the server for the seed
  // and reported "No speech-to-text model is installed" with Parakeet on disk.
  it('retries a failed prefs load instead of pinning the seed model', async () => {
    mocks.state.dictationModelId = 'sherpa-whisper-tiny'; // the store's seed
    let calls = 0;
    mocks.state.loadDictationPrefs = async () => {
      calls += 1;
      if (calls === 1) throw new Error('backend not listening yet');
      mocks.state.dictationModelId = 'sherpa-parakeet-tdt-v3';
      return true;
    };
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(withI18n(<CaptureWidget />));
    await waitFor(() =>
      expect(
        mocks.holder.calls.some(([command]) => command === 'mark_dictation_capture_ready'),
      ).toBe(true),
    );

    const ws = await startNativeSession('after-startup-failure');

    expect(calls).toBeGreaterThanOrEqual(2);
    expect(ws.url).toContain('model=sherpa-parakeet-tdt-v3');
    expect(ws.url).not.toContain('sherpa-whisper-tiny');
  });

  it('picks up a model changed in another window at the next capture', async () => {
    mocks.state.dictationModelId = 'sherpa-whisper-tiny';
    let persisted = 'sherpa-whisper-tiny';
    mocks.state.loadDictationPrefs = async () => {
      mocks.state.dictationModelId = persisted;
      return true;
    };
    render(withI18n(<CaptureWidget />));
    await waitFor(() =>
      expect(
        mocks.holder.calls.some(([command]) => command === 'mark_dictation_capture_ready'),
      ).toBe(true),
    );

    // The user installs and selects Parakeet from Transcriptions, in the main
    // window. This window's store is not the one that changed.
    persisted = 'sherpa-parakeet-tdt-v3';

    const ws = await startNativeSession('after-model-switch');
    expect(ws.url).toContain('model=sherpa-parakeet-tdt-v3');
  });

  it('releases the exact native listener registration on unmount', async () => {
    const view = render(withI18n(<CaptureWidget />));
    await waitFor(() =>
      expect(
        mocks.holder.calls.some(([command]) => command === 'mark_dictation_capture_ready'),
      ).toBe(true),
    );
    const ready = mocks.holder.calls.findLast(
      ([command]) => command === 'mark_dictation_capture_ready',
    );

    view.unmount();

    await waitFor(() =>
      expect(mocks.holder.calls).toContainEqual(['end_dictation_capture_registration', ready[1]]),
    );
  });

  it('honors a hold-mode release while microphone startup is pending', async () => {
    mocks.state.dictationMode = 'hold';
    let resolveMicrophone;
    const getUserMedia = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveMicrophone = resolve;
        }),
    );
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });
    render(withI18n(<CaptureWidget />));

    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'hold-session' },
      });
    });
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce());
    await act(async () => {
      await mocks.holder.handlers['tray-dictate-stop']();
    });
    await act(async () => {
      resolveMicrophone({ getTracks: () => [{ stop() {} }] });
    });

    expect(await screen.findByText(/Transcribing/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText(/Listening/)).not.toBeInTheDocument();
  });

  it('keeps recording for a new hold press after the previous hold was released during startup', async () => {
    mocks.state.dictationMode = 'hold';
    let resolveMicrophone;
    const getUserMedia = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveMicrophone = resolve;
        }),
    );
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    render(withI18n(<CaptureWidget />));
    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));

    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'first-hold-session' },
      });
    });
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce());
    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate-stop']();
    });
    now.mockReturnValue(1400);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'second-hold-session' },
      });
    });

    await act(async () => {
      resolveMicrophone({ getTracks: () => [{ stop() {} }] });
    });

    expect(await screen.findByText(/Listening/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText(/Transcribing/)).not.toBeInTheDocument();
    expect(getUserMedia).toHaveBeenCalledOnce();
  });

  it('honors a hold-mode release while native session activation is pending', async () => {
    mocks.state.dictationMode = 'hold';
    let resolveActivation;
    mocks.holder.activate = () =>
      new Promise((resolve) => {
        resolveActivation = resolve;
      });
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop() {} }] }));
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    render(withI18n(<CaptureWidget />));
    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));

    let startEvent;
    act(() => {
      startEvent = mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'activating-hold-session' },
      });
    });
    await waitFor(() => expect(resolveActivation).toBeTypeOf('function'));
    expect(getUserMedia).not.toHaveBeenCalled();
    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate-stop']();
    });
    await act(async () => {
      resolveActivation();
      await startEvent;
    });

    expect(await screen.findByText(/Transcribing/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText(/Listening/)).not.toBeInTheDocument();
  });

  it('allows one microphone startup and adopts the newest native output session', async () => {
    let resolveMicrophone;
    const getUserMedia = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveMicrophone = resolve;
        }),
    );
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });
    const now = vi.spyOn(Date, 'now');
    render(withI18n(<CaptureWidget />));
    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));

    now.mockReturnValue(1000);
    act(() => {
      mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'session-first' } });
    });
    now.mockReturnValue(1200);
    act(() => {
      mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'session-competing' } });
    });
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled());

    expect(getUserMedia).toHaveBeenCalledOnce();
    expect(
      mocks.holder.calls
        .filter(([command]) => command === 'activate_dictation_output_session')
        .map(([, args]) => args.sessionId),
    ).toEqual(['session-first', 'session-competing']);

    await act(async () => {
      resolveMicrophone({ getTracks: () => [{ stop() {} }] });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0];
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'latest target' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'latest target' }));
    await screen.findByText(/Inserted/);

    expect(pasteCalls()[0][1].sessionId).toBe('session-competing');
    expect(mocks.holder.calls).toContainEqual([
      'finish_dictation_output_session',
      { sessionId: 'session-competing' },
    ]);
    now.mockRestore();
  });

  it('replays an activated start after the previous startup terminates while unwinding', async () => {
    let resolveMicSetup;
    let micSetupAttempt = 0;
    mocks.holder.startMic = (_stream, onFrame) => {
      mocks.holder.onFrame = onFrame;
      micSetupAttempt += 1;
      if (micSetupAttempt > 1) return Promise.resolve(async () => {});
      return new Promise((resolve) => {
        resolveMicSetup = () => resolve(async () => {});
      });
    };
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    render(withI18n(<CaptureWidget />));
    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));

    act(() => {
      void mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'failed-startup' } });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    await waitFor(() => expect(resolveMicSetup).toBeTypeOf('function'));
    await act(async () => {
      await FakeWebSocket.instances[0].msgAsync({
        type: 'final',
        final_kind: 'summary',
        text: '',
        model_silent: 'sherpa-parakeet-tdt-v3',
      });
    });
    await screen.findByText(/Transcription failed/);

    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'replayed-startup' } });
    });
    await act(async () => {
      resolveMicSetup();
      await Promise.resolve();
      await Promise.resolve();
    });

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    await screen.findByText(/Listening/);
    expect(mocks.holder.calls).toContainEqual([
      'activate_dictation_output_session',
      { sessionId: 'replayed-startup' },
    ]);
  });

  it('does not finish a replacement session when the adopted startup then terminates', async () => {
    let resolveMicSetup;
    let micSetupAttempt = 0;
    mocks.holder.startMic = (_stream, onFrame) => {
      mocks.holder.onFrame = onFrame;
      micSetupAttempt += 1;
      if (micSetupAttempt > 1) return Promise.resolve(async () => {});
      return new Promise((resolve) => {
        resolveMicSetup = () => resolve(async () => {});
      });
    };
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    render(withI18n(<CaptureWidget />));
    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));

    act(() => {
      void mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'old-startup' } });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    await waitFor(() => expect(resolveMicSetup).toBeTypeOf('function'));

    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'replacement-startup' },
      });
    });
    await act(async () => {
      await FakeWebSocket.instances[0].msgAsync({
        type: 'final',
        final_kind: 'summary',
        text: '',
        model_silent: 'sherpa-parakeet-tdt-v3',
      });
    });
    await act(async () => {
      resolveMicSetup();
      await Promise.resolve();
      await Promise.resolve();
    });

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    expect(mocks.holder.calls).not.toContainEqual([
      'finish_dictation_output_session',
      { sessionId: 'replacement-startup' },
    ]);
    now.mockRestore();
  });

  it('activates a rapid restart before a late old finish resolves', async () => {
    let resolveFinish;
    const finishPending = new Promise((resolve) => {
      resolveFinish = resolve;
    });
    mocks.holder.finish = () => finishPending;
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop() {} }] }));
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    render(withI18n(<CaptureWidget />));
    const ws = await startNativeSession('finishing-session');

    act(() =>
      ws.msg({
        type: 'final',
        final_kind: 'summary',
        text: '',
        model_silent: 'sherpa-parakeet-tdt-v3',
      }),
    );
    await screen.findByText(/Transcription failed/);
    now.mockReturnValue(1200);
    act(() => {
      mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'fresh-session' } });
    });
    await waitFor(() =>
      expect(mocks.holder.calls).toContainEqual([
        'activate_dictation_output_session',
        { sessionId: 'fresh-session' },
      ]),
    );
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    await act(async () => resolveFinish());
    const freshWs = FakeWebSocket.instances[1];
    act(() => freshWs.msg({ type: 'final', final_kind: 'utterance', text: 'fresh words' }));
    act(() => freshWs.msg({ type: 'final', final_kind: 'summary', text: 'fresh words' }));

    await screen.findByText(/Inserted/);
    expect(pasteCalls().at(-1)[1].sessionId).toBe('fresh-session');
  });

  it('rejects an ignored native candidate while an active session is transcribing', async () => {
    mocks.state.dictationMode = 'hold';
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    render(withI18n(<CaptureWidget />));
    await startNativeSession('active-session');

    now.mockReturnValue(1100);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate-stop']();
    });
    await screen.findByText(/Transcribing/);
    now.mockReturnValue(1300);
    act(() => {
      mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'ignored-candidate' } });
    });

    await waitFor(() =>
      expect(mocks.holder.calls).toContainEqual([
        'reject_dictation_output_session',
        { sessionId: 'ignored-candidate' },
      ]),
    );
    expect(mocks.holder.calls).not.toContainEqual([
      'activate_dictation_output_session',
      { sessionId: 'ignored-candidate' },
    ]);
    expect(mocks.holder.calls).not.toContainEqual([
      'finish_dictation_output_session',
      { sessionId: 'ignored-candidate' },
    ]);
  });

  it('falls back to raw PCM when MediaRecorder cannot be constructed', async () => {
    mocks.state.dictationModelId = null;
    delete global.MediaRecorder;
    render(withI18n(<CaptureWidget />));

    const ws = await startSession();
    expect(ws.url).toContain('/ws/transcribe?pcm=1&sr=16000');
    expect(mocks.authenticatedWsUrl).toHaveBeenCalledWith('/ws/transcribe?pcm=1&sr=16000', {
      apiBase: 'http://test',
    });
    expect(ws.url).toContain('ws_ticket=one-use');
    expect(mocks.holder.onFrame).toBeTypeOf('function');

    act(() => mocks.holder.onFrame(new Float32Array([0.25, -0.25])));
    expect(ws.sent.some((value) => value instanceof ArrayBuffer)).toBe(true);
  });

  it('renders truthful model status from {type:"status"} frames', async () => {
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'status', stage: 'downloading', progress: 0.42 }));
    expect(screen.getByText(/Downloading voice model/)).toBeInTheDocument();
    expect(screen.getByText(/42%/)).toBeInTheDocument();

    act(() => ws.msg({ type: 'status', stage: 'loading' }));
    expect(screen.getByText(/Loading model/)).toBeInTheDocument();

    act(() => ws.msg({ type: 'status', stage: 'ready' }));
    expect(screen.getByText(/Listening/)).toBeInTheDocument();
  });

  it('surfaces a silent selected model as an error instead of successful no-speech', async () => {
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    await act(
      async () =>
        await ws.msgAsync({
          type: 'final',
          final_kind: 'summary',
          text: '',
          model_silent: 'sherpa-parakeet-tdt-v3',
          warning: 'The selected dictation model produced no text.',
        }),
    );

    await screen.findByText(/Transcription failed/);
    expect(screen.queryByText(/No speech detected/)).not.toBeInTheDocument();
    expect(pasteCalls()).toEqual([]);
    await waitFor(() =>
      expect(mocks.holder.calls).toContainEqual([
        'finish_dictation_output_session',
        { sessionId: 'capture-session-1' },
      ]),
    );
  });

  it('shows "Inserted" only after simulate_paste resolved Ok', async () => {
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    // Offline-model shape: one utterance final, then the EOF summary.
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));

    await screen.findByText(/Inserted/);
    expect(pasteCalls().length).toBeGreaterThan(0);
    expect(pasteCalls()[0][1]).toEqual({
      text: 'hello world',
      sessionId: 'capture-session-1',
    });
  });

  it('cancels a pending auto-dismiss when the widget unmounts', async () => {
    const { unmount } = render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    vi.useFakeTimers();
    try {
      await act(async () => {
        ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' });
        ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' });
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByText(/Inserted/)).toBeInTheDocument();
      expect(vi.getTimerCount()).toBeGreaterThan(0);

      unmount();

      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('cancels the fallback and closes its socket when unmounted while transcribing', async () => {
    mocks.state.dictationMode = 'hold';
    mocks.state.dictationModelId = null;
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => ({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const { unmount } = render(withI18n(<CaptureWidget />));
    const ws = await startNativeSession('fallback-unmount-session');

    vi.useFakeTimers();
    try {
      await act(async () => {
        await mocks.holder.handlers['tray-dictate-stop']();
      });

      expect(screen.getByText(/Transcribing/)).toBeInTheDocument();
      expect(vi.getTimerCount()).toBeGreaterThan(0);

      unmount();

      expect(vi.getTimerCount()).toBe(0);
      expect(ws.readyState).toBe(FakeWebSocket.CLOSED);
      expect(stopTrack).toHaveBeenCalledOnce();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(mocks.apiFetch).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('closes an active socket and capture graph when the widget unmounts', async () => {
    const stopTrack = vi.fn();
    const stopMic = vi.fn(async () => {});
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => ({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    mocks.holder.startMic = vi.fn(async (_stream, onFrame) => {
      mocks.holder.onFrame = onFrame;
      return stopMic;
    });
    const { unmount } = render(withI18n(<CaptureWidget />));
    const ws = await startNativeSession('active-unmount-session');

    unmount();

    expect(ws.readyState).toBe(FakeWebSocket.CLOSED);
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(stopMic).toHaveBeenCalledOnce();
  });

  it('releases a microphone stream that resolves after the widget unmounts', async () => {
    let resolveMicrophone;
    const stopTrack = vi.fn();
    const getUserMedia = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveMicrophone = resolve;
        }),
    );
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });
    const { unmount } = render(withI18n(<CaptureWidget />));
    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));

    act(() => {
      void mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'pending-microphone-session' },
      });
    });
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce());

    unmount();
    await act(async () => {
      resolveMicrophone({ getTracks: () => [{ stop: stopTrack }] });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('does not open a socket when its authenticated URL resolves after unmount', async () => {
    let resolveEndpoint;
    mocks.authenticatedWsUrl.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveEndpoint = resolve;
        }),
    );
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => ({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const { unmount } = render(withI18n(<CaptureWidget />));
    await waitFor(() => expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function'));

    act(() => {
      void mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'pending-ticket-session' },
      });
    });
    await waitFor(() => expect(mocks.authenticatedWsUrl).toHaveBeenCalledOnce());

    unmount();
    await act(async () => {
      resolveEndpoint('ws://test/ws/transcribe?ws_ticket=late');
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('keeps native delivery session-bound and never pre-writes the WebView clipboard', async () => {
    render(withI18n(<CaptureWidget />));
    const ws = await startNativeSession('native-session-7');

    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));

    await screen.findByText(/Inserted/);
    expect(mocks.copyText).not.toHaveBeenCalled();
    expect(pasteCalls()).toEqual([
      ['simulate_paste', { text: 'hello world', sessionId: 'native-session-7' }],
    ]);
    expect(mocks.holder.calls).toContainEqual([
      'finish_dictation_output_session',
      { sessionId: 'native-session-7' },
    ]);
  });

  it('uses the WebView clipboard only after native clipboard delivery fails', async () => {
    mocks.state.dictationModelId = null;
    const order = [];
    mocks.holder.paste = async () => {
      order.push('native');
      throw 'clipboard: native clipboard unavailable';
    };
    mocks.copyText.mockImplementation(async (text) => {
      order.push('webview');
      expect(text).toBe('fallback words');
    });
    render(withI18n(<CaptureWidget />));
    const ws = await startNativeSession('wayland-session');

    act(() => ws.msg({ type: 'final', text: 'fallback words' }));

    await screen.findByText(/Copied/);
    expect(order).toEqual(['native', 'webview']);
    expect(pasteCalls()).toEqual([
      ['simulate_paste', { text: 'fallback words', sessionId: 'wayland-session' }],
    ]);
  });

  it('never lets an old legacy delivery finish a restarted native session', async () => {
    mocks.state.dictationModelId = null;
    let resolveOldPaste;
    mocks.holder.paste = () =>
      new Promise((resolve) => {
        resolveOldPaste = resolve;
      });
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    const { container } = render(withI18n(<CaptureWidget />));
    const oldWs = await startNativeSession('old-legacy-session');

    act(() => oldWs.msg({ type: 'final', text: 'old legacy words' }));
    await waitFor(() => expect(pasteCalls()).toHaveLength(1));
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(container.querySelector('.capture-pill')).toBeNull());

    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'new-legacy-session' },
      });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    await screen.findByText(/Listening/);

    await act(async () => {
      resolveOldPaste('inserted');
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText(/Listening/)).toBeInTheDocument();
    expect(mocks.holder.calls).not.toContainEqual([
      'finish_dictation_output_session',
      { sessionId: 'new-legacy-session' },
    ]);
  });

  it('discards an old deferred POST result after cancel and restart', async () => {
    mocks.state.dictationModelId = null;
    let resolveOldPost;
    mocks.apiFetch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveOldPost = resolve;
        }),
    );
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    const { container } = render(withI18n(<CaptureWidget />));
    const oldWs = await startNativeSession('old-post-session');

    act(() => oldWs.msg({ type: 'error', kind: 'server', message: 'socket failed' }));
    await waitFor(() => expect(mocks.apiFetch).toHaveBeenCalledOnce());
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(container.querySelector('.capture-pill')).toBeNull());

    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'new-post-session' } });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    await screen.findByText(/Listening/);

    await act(async () => {
      resolveOldPost({ json: async () => ({ text: 'old post words' }) });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText(/Listening/)).toBeInTheDocument();
    expect(pasteCalls()).toEqual([]);
    expect(mocks.holder.calls).not.toContainEqual([
      'finish_dictation_output_session',
      { sessionId: 'new-post-session' },
    ]);
  });

  it('an "a11y:" paste rejection renders the actionable error, never "Inserted"', async () => {
    mocks.holder.paste = async () => {
      throw 'a11y: process is not trusted';
    };
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));

    await screen.findByText(/Accessibility access needed/);
    expect(screen.queryByText(/Inserted/)).not.toBeInTheDocument();
    expect(mocks.copyText).not.toHaveBeenCalled();

    // The action button opens the OS Accessibility pane.
    fireEvent.click(screen.getByText('Open Settings'));
    await waitFor(() =>
      expect(mocks.holder.calls.some(([c]) => c === 'open_accessibility_settings')).toBe(true),
    );
  });

  it('Esc during recording aborts: socket closed, nothing pasted, pill gone', async () => {
    const { container } = render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(container.querySelector('.capture-pill')).toBeNull());
    expect(ws.readyState).toBe(FakeWebSocket.CLOSED);
    expect(pasteCalls()).toEqual([]);
  });

  it('live retract-retype is OFF by default: partials never simulate_type', async () => {
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'partial', text: 'hel' }));
    act(() => ws.msg({ type: 'partial', text: 'hello wor' }));
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));

    await screen.findByText(/Inserted/);
    // Committed final went through the paste path; no keystroke storms.
    expect(typeCalls()).toEqual([]);
    expect(pasteCalls().length).toBeGreaterThan(0);
  });

  it('the LS_LIVE_TYPING pref opts back into word-by-word typing', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'partial', text: 'hello' }));
    await waitFor(() => expect(typeCalls().length).toBeGreaterThan(0));
    expect(typeCalls()[0][1]).toEqual({
      text: 'hello',
      backspaces: 0,
      sessionId: 'capture-session-1',
    });
  });

  it('never runs an old queued live-type delta against a restarted native session', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    let resolveFirstType;
    mocks.holder.type = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveFirstType = resolve;
        }),
    );
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    const { container } = render(withI18n(<CaptureWidget />));
    const oldWs = await startNativeSession('old-type-session');

    act(() => oldWs.msg({ type: 'partial', text: 'hello' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(1));
    act(() => oldWs.msg({ type: 'partial', text: 'hello world' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(container.querySelector('.capture-pill')).toBeNull());

    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'new-type-session' },
      });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    await screen.findByText(/Listening/);

    await act(async () => {
      resolveFirstType('inserted');
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(typeCalls()).toHaveLength(1);
    expect(typeCalls()[0][1].sessionId).toBe('old-type-session');
  });

  it('never lets an old utterance commit corrupt a restarted live-type prefix', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    let resolveOldType;
    mocks.holder.type = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOldType = resolve;
          }),
      )
      .mockResolvedValue('inserted');
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    const { container } = render(withI18n(<CaptureWidget />));
    const oldWs = await startNativeSession('old-commit-session');

    act(() => oldWs.msg({ type: 'partial', text: 'old' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(1));
    act(() => oldWs.msg({ type: 'final', final_kind: 'utterance', text: 'old words' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(container.querySelector('.capture-pill')).toBeNull());

    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({ payload: { sessionId: 'new-commit-session' } });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    const newWs = FakeWebSocket.instances[1];
    await act(async () => {
      resolveOldType('inserted');
      await Promise.resolve();
      await Promise.resolve();
    });
    act(() => newWs.msg({ type: 'partial', text: 'fresh' }));

    await waitFor(() => expect(typeCalls()).toHaveLength(2));
    expect(typeCalls()[1][1]).toEqual({
      text: 'fresh',
      backspaces: 0,
      sessionId: 'new-commit-session',
    });
  });

  it('copies the summary without pasting after a later live-type delta fails', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    let typeAttempt = 0;
    mocks.holder.type = async () => {
      typeAttempt += 1;
      if (typeAttempt === 2) throw 'paste: input synthesis failed';
      return 'inserted';
    };
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'partial', text: 'hello' }));
    act(() => ws.msg({ type: 'partial', text: 'hello world' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(2));
    await act(async () => Promise.resolve());
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));

    await screen.findByText(/Copied/);
    expect(pasteCalls()).toEqual([]);
    expect(copyCalls()).toEqual([
      ['copy_dictation_output_session', { text: 'hello world', sessionId: 'capture-session-1' }],
    ]);
  });

  it('copies the summary when the first live-type call may have partially emitted', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    mocks.holder.type = async () => {
      throw 'paste: input synthesis failed';
    };
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'partial', text: 'hello world' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(1));
    await act(async () => Promise.resolve());
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));

    await screen.findByText(/Copied/);
    expect(pasteCalls()).toEqual([]);
    expect(copyCalls()).toEqual([
      ['copy_dictation_output_session', { text: 'hello world', sessionId: 'capture-session-1' }],
    ]);
  });

  it('downgrades a zero-emission live-type preflight failure to committed copy', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    mocks.holder.type = async () => {
      throw 'preflight: native session is clipboard-only';
    };
    mocks.holder.paste = async () => 'copied';
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'partial', text: 'hello world' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(1));
    await act(async () => Promise.resolve());
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));

    await screen.findByText(/Copied/);
    expect(pasteCalls().map(([, args]) => args.text)).toEqual(['hello world', 'hello world']);
  });

  it('commits an utterance when its pending first live-type call fails preflight', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    let rejectType;
    mocks.holder.type = () =>
      new Promise((_resolve, reject) => {
        rejectType = reject;
      });
    mocks.holder.paste = async () => 'copied';
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'partial', text: 'hello world' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(1));
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));
    expect(pasteCalls()).toEqual([]);

    await act(async () => rejectType('preflight: native session is clipboard-only'));

    await screen.findByText(/Copied/);
    expect(pasteCalls().map(([, args]) => args.text)).toEqual(['hello world', 'hello world']);
  });

  it('copies the full summary without reinserting when preflight fails after a live prefix', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    let typeAttempt = 0;
    mocks.holder.type = async () => {
      typeAttempt += 1;
      if (typeAttempt === 2) throw 'preflight: native input became unavailable';
      return 'inserted';
    };
    mocks.holder.paste = async () => 'copied';
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'partial', text: 'hello' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(1));
    act(() => ws.msg({ type: 'partial', text: 'hello world' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(2));
    await act(async () => Promise.resolve());
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));

    await screen.findByText(/Copied/);
    expect(pasteCalls()).toEqual([]);
    expect(copyCalls()).toEqual([
      ['copy_dictation_output_session', { text: 'hello world', sessionId: 'capture-session-1' }],
    ]);
  });

  it('waits for an in-flight native type result before delivering a summary', async () => {
    localStorage.setItem('omni_capture_live_typing', '1');
    let rejectType;
    mocks.holder.type = () =>
      new Promise((_resolve, reject) => {
        rejectType = reject;
      });
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'partial', text: 'hello world' }));
    await waitFor(() => expect(typeCalls()).toHaveLength(1));
    await act(async () => {
      ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' });
      await Promise.resolve();
    });

    expect(pasteCalls()).toEqual([]);
    await act(async () => rejectType('paste: input synthesis failed'));
    await screen.findByText(/Copied/);
    expect(pasteCalls()).toEqual([]);
    expect(copyCalls()).toEqual([
      ['copy_dictation_output_session', { text: 'hello world', sessionId: 'capture-session-1' }],
    ]);
  });

  it('a refined EOF summary is not re-pasted as a new utterance', async () => {
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    // Two per-utterance commits paste live…
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'Hello world.' }));
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'Second bit.' }));
    // …then the EOF summary arrives with an LLM-refined variant. Its raw
    // `text` equals the committed join, so it must finalise — never paste
    // the whole (refined) transcript a third time.
    act(() =>
      ws.msg({
        type: 'final',
        final_kind: 'summary',
        text: 'Hello world. Second bit.',
        refined_text: 'Hello world, second bit.',
      }),
    );

    await screen.findByText(/Inserted/);
    expect(pasteCalls().map(([, a]) => a.text)).toEqual(['Hello world.', ' Second bit.']);
  });

  it('commits repeated identical utterances instead of mistaking the second for EOF', async () => {
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'Yes.' }));
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'Yes.' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'Yes. Yes.' }));

    await screen.findByText(/Inserted/);
    expect(pasteCalls().map(([, args]) => args.text)).toEqual(['Yes.', ' Yes.']);
  });

  it('delivers only an uncommitted EOF-summary tail', async () => {
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'First.' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'First. Tail.' }));

    await screen.findByText(/Inserted/);
    expect(pasteCalls().map(([, args]) => args.text)).toEqual(['First.', ' Tail.']);
  });

  it('keeps copied as the session outcome and refreshes the full summary once', async () => {
    let copied = false;
    mocks.holder.paste = async (_cmd, { text }) => {
      if (text === ' Second.') copied = true;
      return copied ? 'copied' : 'inserted';
    };
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'First.' }));
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'Second.' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'First. Second.' }));

    await screen.findByText(/Copied/);
    expect(pasteCalls().map(([, args]) => args.text)).toEqual([
      'First.',
      ' Second.',
      'First. Second.',
    ]);
    expect(mocks.copyText).not.toHaveBeenCalled();
  });

  it('refreshes a WebView clipboard fallback without retrying native insertion', async () => {
    let attempt = 0;
    mocks.holder.paste = async () => {
      attempt += 1;
      if (attempt === 1) return 'inserted';
      if (attempt === 2) throw 'clipboard: native clipboard unavailable';
      return 'inserted';
    };
    render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'First.' }));
    await waitFor(() => expect(pasteCalls()).toHaveLength(1));
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'Second.' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'First. Second.' }));

    await screen.findByText(/Copied/);
    expect(pasteCalls().map(([, args]) => args.text)).toEqual(['First.', ' Second.']);
    expect(mocks.copyText).toHaveBeenNthCalledWith(1, ' Second.');
    expect(mocks.copyText).toHaveBeenNthCalledWith(2, 'First. Second.');
  });

  it('never lets an old deferred delivery finish or mutate a restarted session', async () => {
    let resolveOldPaste;
    let pasteAttempt = 0;
    mocks.holder.paste = () => {
      pasteAttempt += 1;
      if (pasteAttempt === 1) {
        return new Promise((resolve) => {
          resolveOldPaste = resolve;
        });
      }
      return Promise.resolve('inserted');
    };
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    const { container } = render(withI18n(<CaptureWidget />));
    const oldWs = await startNativeSession('old-delivery-session');

    act(() => oldWs.msg({ type: 'final', final_kind: 'utterance', text: 'old words' }));
    await waitFor(() => expect(pasteCalls()).toHaveLength(1));
    act(() => oldWs.msg({ type: 'final', final_kind: 'summary', text: 'old words' }));
    await act(async () => Promise.resolve());
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(container.querySelector('.capture-pill')).toBeNull());

    now.mockReturnValue(1200);
    await act(async () => {
      await mocks.holder.handlers['tray-dictate']({
        payload: { sessionId: 'new-delivery-session' },
      });
    });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    await screen.findByText(/Listening/);

    await act(async () => {
      resolveOldPaste('copied');
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText(/Listening/)).toBeInTheDocument();
    expect(pasteCalls()).toEqual([
      ['simulate_paste', { text: 'old words', sessionId: 'old-delivery-session' }],
    ]);
    expect(mocks.holder.calls).not.toContainEqual([
      'finish_dictation_output_session',
      { sessionId: 'new-delivery-session' },
    ]);
  });

  it('renders the one-time Accessibility setup state when the mount probe fails', async () => {
    mocks.holder.a11y = false;
    render(withI18n(<CaptureWidget />));

    await screen.findByText(/Allow Accessibility/);
    expect(screen.getByText('Open Settings')).toBeInTheDocument();
    // It does not pretend to record.
    expect(screen.queryByText(/Listening/)).not.toBeInTheDocument();
  });

  it('exposes the full setup label as a title, since the 300px pill clips it', async () => {
    mocks.holder.a11y = false;
    render(withI18n(<CaptureWidget />));

    const labelEl = await screen.findByText(/Allow Accessibility/);
    expect(labelEl.title).toBe(i18n.t('capture.a11y_setup'));
  });

  it('clears the Accessibility setup pill after the native grant changes', async () => {
    vi.useFakeTimers();
    try {
      mocks.holder.a11y = false;
      render(withI18n(<CaptureWidget />));

      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getByText(/Allow Accessibility/)).toBeInTheDocument();

      mocks.holder.a11y = true;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100);
      });

      expect(screen.queryByText(/Allow Accessibility/)).not.toBeInTheDocument();
      // The unfocusable widget must also leave the screen — asserting only the
      // pill text would still pass if hideWidgetWindow() were dropped.
      expect(mocks.holder.hideWindow).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('records from the Accessibility setup state and reports native clipboard fallback', async () => {
    mocks.holder.a11y = false;
    mocks.holder.paste = async () => 'copied';
    render(withI18n(<CaptureWidget />));
    await screen.findByText(/Allow Accessibility/);
    expect(screen.getByText('Open Settings')).toBeInTheDocument();

    const ws = await startNativeSession('a11y-copy-session');
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'clipboard words' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'clipboard words' }));

    await screen.findByText(/Copied/);
    expect(pasteCalls().map(([, args]) => args)).toEqual([
      { text: 'clipboard words', sessionId: 'a11y-copy-session' },
      { text: 'clipboard words', sessionId: 'a11y-copy-session' },
    ]);
  });

  it('waveform bars move from the worklet mic frames', async () => {
    const { container } = render(withI18n(<CaptureWidget />));
    await startSession();
    expect(mocks.holder.onFrame).toBeTypeOf('function');

    // Feed ~5 frames of speech-level audio (≈100 ms at 20 ms/frame).
    for (let i = 0; i < 5; i++) mocks.holder.onFrame(new Float32Array(320).fill(0.5));

    await waitFor(() => {
      const bars = container.querySelectorAll('.capture-pill__wave-bar');
      expect(bars.length).toBe(12);
      const heights = [...bars].map((b) => parseInt(b.style.height, 10));
      expect(Math.max(...heights)).toBeGreaterThan(12); // above the silence floor
    });
  });

  // ── The pill must never strand ─────────────────────────────────────────
  // Errors deliberately do not auto-dismiss, so a failed paste keeps the
  // transcript on screen for the user to copy. But the mic / model-missing /
  // server / connection paths have NO transcript to rescue, and those left the
  // widget parked on top of everything until the app was restarted — reported
  // as "the dictation bubble is permanently sticking when it's not used".
  // The distinction is the whole fix, so both halves are pinned here.

  it('an error with nothing to rescue dismisses itself', async () => {
    // Start the session on REAL timers — startSession() awaits, and fake
    // timers would stall those awaits forever.
    const { container } = render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    vi.useFakeTimers();
    try {
      // A server-side failure carrying no transcript.
      act(() => ws.msg({ type: 'error', kind: 'server', message: 'backend went away' }));
      expect(container.querySelector('.capture-pill')).not.toBeNull();

      // Long enough to read, then gone — without the user touching anything.
      await act(async () => {
        vi.advanceTimersByTime(9000);
      });
      expect(container.querySelector('.capture-pill')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("an error holding a transcript stays up — that text is the user's to copy", async () => {
    mocks.holder.paste = async () => {
      throw 'a11y: process is not trusted';
    };
    const { container } = render(withI18n(<CaptureWidget />));
    const ws = await startSession();

    // Drive to the error state on real timers so the paste rejection settles.
    act(() => ws.msg({ type: 'final', final_kind: 'utterance', text: 'hello world' }));
    act(() => ws.msg({ type: 'final', final_kind: 'summary', text: 'hello world' }));
    await screen.findByText(/Accessibility access needed/);

    vi.useFakeTimers();
    try {
      // Well past the no-transcript dismissal window.
      await act(async () => {
        vi.advanceTimersByTime(30000);
      });
      // Still there: auto-dismissing would silently discard the only copy of
      // what the user just said.
      expect(container.querySelector('.capture-pill')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
