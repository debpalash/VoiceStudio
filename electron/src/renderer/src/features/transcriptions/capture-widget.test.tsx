import { act, render } from '@testing-library/react';
import { useSyncExternalStore } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CaptureWidget } from './capture-widget';

// A real external store, not a plain object read: without a subscription the
// component never re-renders on a stage change, and these assertions would
// pass even against the bug they exist to catch.
const backendStore = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  // Closure state, not `this`: these are handed to React as bare callbacks,
  // where `this` is undefined in a strict-mode module.
  let stage = 'unresponsive';
  return {
    reset: () => {
      stage = 'unresponsive';
    },
    set: (next: string) => {
      if (stage === next) return;
      stage = next;
      for (const listener of listeners) listener();
    },
    get: () => stage,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
});

// The real predicate is deliberately NOT stubbed: whether a busy backend counts
// as reachable is exactly what this test pins down.
vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => {
    // Subscribing is what makes a stage change re-render the widget, which is
    // the entire mechanism the regression depended on.
    useSyncExternalStore(backendStore.subscribe, backendStore.get, backendStore.get);
    return { stage: backendStore.get(), baseUrl: 'http://127.0.0.1:3900' };
  },
}));

const capture = vi.hoisted(() => ({
  accept: vi.fn(async () => {}),
  cancel: vi.fn(async () => {}),
  deliver: vi.fn(async () => 'copied'),
  onEvent: vi.fn(),
  phase: vi.fn(async () => {}),
  ready: vi.fn(async () => {}),
}));

const dictation = vi.hoisted(() => {
  // `useSyncExternalStore` compares snapshots with Object.is, so this must be
  // one stable object — a fresh literal per call loops React forever.
  const snapshot = { stage: 'recording', text: '', paused: false };
  return {
    cancel: vi.fn(),
    pause: vi.fn(),
    start: vi.fn(async () => {}),
    stop: vi.fn(async () => {}),
    getSnapshot: () => snapshot,
    subscribe: () => () => {},
  };
});

// `CaptureWidget` does `new LiveDictation()`, so the mock must be a real
// constructor whose instances share the spies above.
vi.mock('./live-dictation', () => ({
  LiveDictation: class {
    cancel = dictation.cancel;
    pause = dictation.pause;
    start = dictation.start;
    stop = dictation.stop;
    getSnapshot = dictation.getSnapshot;
    subscribe = dictation.subscribe;
  },
}));
vi.mock('@shared/utils/transcriptionsStore', () => ({ addTranscription: vi.fn() }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

/** Let the effect subscribe, then push a `start`-shaped capture event into it. */
async function mountAndStartSession(): Promise<void> {
  const onEvent = capture.onEvent.mock.calls.at(-1)?.[0] as ((e: unknown) => void) | undefined;
  if (!onEvent) throw new Error('capture widget never subscribed to native events');
  await act(async () => {
    onEvent({ action: 'start', session: 7 });
  });
}

beforeEach(() => {
  backendStore.reset();
  for (const target of [capture, dictation]) {
    for (const value of Object.values(target)) {
      if (typeof value === 'function' && 'mockClear' in value) value.mockClear();
    }
  }
  capture.onEvent.mockReturnValue(() => {});
  vi.stubGlobal('voicestudio', { capture });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// #2430 — a busy backend is a live backend, so dictation may start during the
// busy window. The regression this pins: subscribing on `unresponsive` while
// still listing `backend.stage` as an effect dependency meant the very next
// successful health probe flipped the stage to `ready`, re-ran the effect, and
// ran its cleanup — which cancels the recording and tells main to drop the
// session. The dictation died on recovery, before transcription could finish.
describe('native dictation survives a busy backend recovering (#2430)', () => {
  it('keeps the accepted session when a health probe restores ready', async () => {
    render(<CaptureWidget />);
    await mountAndStartSession();
    expect(capture.accept).toHaveBeenCalledWith(7);

    // The supervisor retires `unresponsive` back to `ready` on the next probe.
    await act(async () => backendStore.set('ready'));
    await act(async () => {});

    expect(dictation.cancel).not.toHaveBeenCalled();
    expect(capture.cancel).not.toHaveBeenCalled();
  });

  it('also survives a second busy window without resubscribing', async () => {
    render(<CaptureWidget />);
    await mountAndStartSession();
    const subscriptions = capture.onEvent.mock.calls.length;

    for (const stage of ['ready', 'unresponsive', 'ready', 'unresponsive']) {
      await act(async () => backendStore.set(stage));
    }

    expect(dictation.cancel).not.toHaveBeenCalled();
    expect(capture.cancel).not.toHaveBeenCalled();
    // A stable subscription, not one torn down and rebuilt per stage change.
    expect(capture.onEvent).toHaveBeenCalledTimes(subscriptions);
  });

  it('still tears the session down when the backend is genuinely lost', async () => {
    render(<CaptureWidget />);
    await mountAndStartSession();

    await act(async () => backendStore.set('crashed'));

    expect(capture.cancel).toHaveBeenCalledWith(7);
    expect(dictation.cancel).toHaveBeenCalled();
  });
});
