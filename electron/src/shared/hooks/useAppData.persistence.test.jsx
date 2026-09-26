import React, { StrictMode, Suspense } from 'react';
import { act, cleanup, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const persistenceProbe = vi.hoisted(() => ({
  flushOnNextOmniQueue: false,
  providerReads: 0,
  queuedProviders: [],
  disposers: [],
  materializedValues: [],
}));

// Keep the real scheduler in this integration suite. The wrapper adds one
// deterministic test seam: forcing the first omni_ui registration to flush
// synchronously catches an initial-default provider before React can replace
// it with the restored render's provider.
vi.mock('../utils/coalescedJsonStorage', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    queueJsonWrite(key, readLatestValue) {
      const trackedProvider = () => {
        persistenceProbe.providerReads += 1;
        const value = readLatestValue();
        persistenceProbe.materializedValues.push(value);
        return value;
      };
      const disposeActual = actual.queueJsonWrite(key, trackedProvider);
      const dispose = vi.fn(disposeActual);
      persistenceProbe.queuedProviders.push({ key, provider: trackedProvider });
      persistenceProbe.disposers.push(dispose);
      if (key === 'omni_ui' && persistenceProbe.flushOnNextOmniQueue) {
        persistenceProbe.flushOnNextOmniQueue = false;
        actual.flushPendingWrites();
      }
      return dispose;
    },
  };
});

const systemApi = vi.hoisted(() => ({ modelStatus: vi.fn() }));
vi.mock('../api/system', () => systemApi);
vi.mock('../api/hooks', () => ({
  useModelStatus: () => ({ data: { status: 'idle' } }),
}));
vi.mock('./useRealtimeEvents', () => ({ default: vi.fn() }));

import useAppData from './useAppData';
import { useAppStore } from '../store';
import {
  configurePersistenceRole,
  discardPendingWrites,
  flushPendingWrites,
  resetCoalescedJsonStorageForTests,
} from '../utils/coalescedJsonStorage';

const initialStoreState = useAppStore.getInitialState();
const OMNI_UI_KEYS = [
  'uiScale',
  'text',
  'mode',
  'defineMethod',
  'vdStates',
  'language',
  'isSidebarCollapsed',
  'sidebarTab',
  'dubJobId',
  'dubFilename',
  'dubDuration',
  'dubSegments',
  'dubLang',
  'dubLangCode',
  'dubTracks',
  'dubStep',
  'dubTranscript',
  'exportTracks',
  'preserveBg',
  'defaultTrack',
  'exportHistory',
  'speed',
  'steps',
  'cfg',
  'denoise',
  'showOverrides',
];

function resetProbe() {
  persistenceProbe.flushOnNextOmniQueue = false;
  persistenceProbe.providerReads = 0;
  persistenceProbe.queuedProviders.length = 0;
  persistenceProbe.disposers.length = 0;
  persistenceProbe.materializedValues.length = 0;
}

function seedOmniUi(value) {
  localStorage.setItem('omni_ui', JSON.stringify(value));
}

function watchStorageWrites() {
  return vi.spyOn(localStorage, 'setItem');
}

function omniWrites(setItemSpy) {
  return setItemSpy.mock.calls
    .filter(([key]) => key === 'omni_ui')
    .map(([, raw]) => JSON.parse(raw));
}

beforeEach(() => {
  vi.useFakeTimers();
  resetCoalescedJsonStorageForTests();
  configurePersistenceRole('main');
  useAppStore.setState(initialStoreState, true);
  useAppStore.setState({
    text: 'initial default that must never win',
    mode: 'studio',
    dubSegments: [],
    dubStep: 'idle',
  });
  discardPendingWrites();
  localStorage.clear();
  resetProbe();
  // Keep the backend-readiness loop parked without scheduling retries or
  // allowing unrelated list responses to update the hook after a test ends.
  systemApi.modelStatus.mockReset().mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
  cleanup();
  resetCoalescedJsonStorageForTests();
  localStorage.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useAppData omni_ui persistence', () => {
  it('never exposes initial defaults to a lifecycle flush while restoring seeded state', () => {
    seedOmniUi({
      uiScale: 1.15,
      text: 'restored script',
      mode: 'dub',
      defineMethod: 'design',
      language: 'Spanish',
      isSidebarCollapsed: true,
      sidebarTab: 'projects',
      dubJobId: 'job-restored',
      dubFilename: 'clip.mp4',
      dubDuration: 42,
      dubSegments: [{ id: '1', text: 'Hola', start: 0, end: 2 }],
      dubLang: 'Spanish',
      dubLangCode: 'es',
      dubTracks: ['es'],
      dubStep: 'generating',
      dubTranscript: 'Hola',
      exportTracks: { original: true, es: true },
      preserveBg: false,
      defaultTrack: 'es',
      exportHistory: [{ id: 'export-1' }],
      speed: 1.2,
      steps: 24,
      cfg: 2.5,
      denoise: false,
      showOverrides: true,
    });
    const setItemSpy = watchStorageWrites();
    setItemSpy.mockClear();
    persistenceProbe.flushOnNextOmniQueue = true;

    const { result } = renderHook(() => useAppData());

    const writes = omniWrites(setItemSpy);
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({
      text: 'restored script',
      mode: 'dub',
      dubJobId: 'job-restored',
      dubStep: 'editing',
      exportHistory: [{ id: 'export-1' }],
      showOverrides: true,
    });
    expect(writes[0].dubSegments).toEqual([
      { id: '1', text: 'Hola', text_original: 'Hola', start: 0, end: 2 },
    ]);
    expect(result.current.showOverrides).toBe(true);
    expect(Object.keys(persistenceProbe.materializedValues[0])).toEqual(OMNI_UI_KEYS);
    expect(persistenceProbe.queuedProviders[0].key).toBe('omni_ui');
  });

  it("normalizes the legacy persisted 'queue' mode to 'batch' on restore", () => {
    // Regression: App.jsx used to switch on mode === 'queue' (an id nothing
    // could set, since the store's Mode union says 'batch'); any persisted
    // 'queue' would strand the restore on an unrenderable mode.
    seedOmniUi({ mode: 'queue' });

    renderHook(() => useAppData());

    expect(useAppStore.getState().mode).toBe('batch');
  });

  it('keeps serialization and physical writes out of a burst and flushes only the latest value', () => {
    const setItemSpy = watchStorageWrites();
    renderHook(() => useAppData());
    flushPendingWrites();
    setItemSpy.mockClear();
    persistenceProbe.providerReads = 0;
    persistenceProbe.materializedValues.length = 0;

    for (let index = 1; index <= 20; index += 1) {
      act(() => {
        useAppStore.getState().setText(`draft-${index}`);
        useAppStore
          .getState()
          .setDubSegments([
            { id: '1', text: `segment-${index}`, text_original: 'source', start: 0, end: 1 },
          ]);
      });
    }

    expect(persistenceProbe.providerReads).toBe(0);
    expect(omniWrites(setItemSpy)).toHaveLength(0);

    act(() => {
      vi.advanceTimersByTime(249);
    });
    expect(omniWrites(setItemSpy)).toHaveLength(0);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    const writes = omniWrites(setItemSpy);
    expect(writes).toHaveLength(1);
    expect(writes[0].text).toBe('draft-20');
    expect(writes[0].dubSegments[0].text).toBe('segment-20');
    expect(persistenceProbe.providerReads).toBe(1);
  });

  it('preserves the original hard deadline during continuous edits', () => {
    const setItemSpy = watchStorageWrites();
    renderHook(() => useAppData());
    flushPendingWrites();
    setItemSpy.mockClear();
    persistenceProbe.providerReads = 0;

    act(() => {
      useAppStore.getState().setText('continuous-1');
    });
    for (let index = 2; index <= 5; index += 1) {
      act(() => {
        vi.advanceTimersByTime(200);
        useAppStore.getState().setText(`continuous-${index}`);
      });
    }

    // Every edit arrived before the 250 ms quiet delay. The maximum timer
    // still belongs to the window opened at t=0 and must not be restarted by
    // React's dependency-effect cleanup/re-registration cycle.
    act(() => {
      vi.advanceTimersByTime(199);
    });
    expect(omniWrites(setItemSpy)).toHaveLength(0);
    expect(persistenceProbe.providerReads).toBe(0);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    const writes = omniWrites(setItemSpy);
    expect(writes).toHaveLength(1);
    expect(writes[0].text).toBe('continuous-5');
    expect(persistenceProbe.providerReads).toBe(1);
  });

  it('never persists state from a render that React abandons', () => {
    let suspendNextRender = false;
    const neverSettles = new Promise(() => {});
    function ConcurrentHarness() {
      useAppData();
      if (suspendNextRender) throw neverSettles;
      return null;
    }

    render(
      <Suspense fallback={null}>
        <ConcurrentHarness />
      </Suspense>,
    );
    act(() => {
      flushPendingWrites();
      useAppStore.getState().setText('last committed script');
    });

    // The store notification starts a render which suspends before commit.
    // A render-time ref assignment exposed this value to the already-pending
    // provider even though React never published it to the UI.
    suspendNextRender = true;
    act(() => {
      useAppStore.getState().setText('abandoned candidate');
    });
    act(() => {
      flushPendingWrites();
    });

    expect(JSON.parse(localStorage.getItem('omni_ui')).text).toBe('last committed script');
  });

  it('uses generation-bound cleanup across StrictMode updates and unmount', () => {
    const setItemSpy = watchStorageWrites();
    const { unmount } = renderHook(() => useAppData(), { wrapper: StrictMode });
    expect(persistenceProbe.disposers.length).toBeGreaterThan(0);

    const obsoleteDisposer = persistenceProbe.disposers.at(-1);
    act(() => {
      useAppStore.getState().setText('newer provider');
    });
    // Dependency changes replace the provider without disposing the prior
    // registration; disposing here would restart the scheduler's hard window.
    expect(obsoleteDisposer).not.toHaveBeenCalled();

    // A repeated/stale cleanup must not cancel the replacement registration.
    obsoleteDisposer();
    act(() => {
      flushPendingWrites();
    });
    expect(omniWrites(setItemSpy).at(-1)?.text).toBe('newer provider');

    setItemSpy.mockClear();
    act(() => {
      useAppStore.getState().setText('cancel on unmount');
    });
    const activeDisposer = persistenceProbe.disposers.at(-1);
    unmount();
    expect(activeDisposer).toHaveBeenCalledOnce();
    act(() => {
      vi.runAllTimers();
      flushPendingWrites();
    });
    expect(omniWrites(setItemSpy)).toHaveLength(0);
  });

  it('completes restore readiness after malformed legacy JSON', () => {
    localStorage.setItem('omni_ui', '{malformed');
    const setItemSpy = watchStorageWrites();
    setItemSpy.mockClear();

    expect(() => renderHook(() => useAppData())).not.toThrow();
    act(() => {
      vi.advanceTimersByTime(250);
    });

    const writes = omniWrites(setItemSpy);
    expect(writes).toHaveLength(1);
    expect(writes[0].text).toBe('initial default that must never win');
  });
});
