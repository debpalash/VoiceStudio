/**
 * Store wiring for the backend-backed dictation prefs (prefsSlice).
 *
 * Asserts the contract the Voice panel + CaptureWidget depend on:
 *   • loadDictationPrefs() hydrates enabled/mode/model from GET /dictation/prefs
 *   • each setter is optimistic AND write-throughs to POST /dictation/prefs
 *   • the store re-syncs from the POST response (so a server-side normalisation
 *     like repo_id → canonical id can't leave the UI out of step)
 *   • a failed write rolls the optimistic value back
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock the api client BEFORE importing the store (prefsSlice imports it at
// module load). Each test swaps the implementations via the spies below.
const apiJson = vi.fn();
const apiPost = vi.fn();
vi.mock('../api/client', () => ({
  apiJson: (...a: any[]) => apiJson(...a),
  apiPost: (...a: any[]) => apiPost(...a),
}));

import { useAppStore } from '../store';
import { createPrefsSlice } from '../store/prefsSlice';

function flush() {
  // Let the write-through promise (.then) settle.
  return new Promise((r) => setTimeout(r, 0));
}

describe('dictation prefs store wiring', () => {
  it('seeds the cross-platform default before backend hydration', () => {
    const slice = createPrefsSlice(vi.fn() as any, vi.fn() as any, {} as any);
    expect(slice.dictationModelId).toBe('sherpa-whisper-tiny');
  });

  beforeEach(() => {
    apiJson.mockReset();
    apiPost.mockReset();
    // Reset to slice seeds.
    useAppStore.setState({
      dictationEnabled: true,
      dictationMode: 'toggle',
      dictationModelId: 'sherpa-whisper-tiny',
      dictationLoaded: false,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it('hydrates from GET /dictation/prefs', async () => {
    apiJson.mockResolvedValue({ enabled: false, mode: 'hold', model_id: 'sherpa-whisper-tiny' });
    await useAppStore.getState().loadDictationPrefs();
    const s = useAppStore.getState();
    expect(apiJson).toHaveBeenCalledWith('/dictation/prefs');
    expect(s.dictationEnabled).toBe(false);
    expect(s.dictationMode).toBe('hold');
    expect(s.dictationModelId).toBe('sherpa-whisper-tiny');
    expect(s.dictationLoaded).toBe(true);
  });

  it('still marks loaded when the backend route fails (older build)', async () => {
    apiJson.mockRejectedValue(new Error('404'));
    await useAppStore.getState().loadDictationPrefs();
    expect(useAppStore.getState().dictationLoaded).toBe(true);
    // Seeds preserved.
    expect(useAppStore.getState().dictationMode).toBe('toggle');
  });

  it('setDictationMode is optimistic and write-throughs, then re-syncs', async () => {
    apiPost.mockResolvedValue({ enabled: true, mode: 'hold', model_id: 'sherpa-parakeet-tdt-v3' });
    useAppStore.getState().setDictationMode('hold');
    // Optimistic update happened synchronously.
    expect(useAppStore.getState().dictationMode).toBe('hold');
    await flush();
    expect(apiPost).toHaveBeenCalledWith('/dictation/prefs', { mode: 'hold' });
    expect(useAppStore.getState().dictationMode).toBe('hold');
  });

  it('re-syncs the model id from the POST response (repo_id → canonical id)', async () => {
    // The user picks via repo_id; the backend normalises to the canonical id.
    apiPost.mockResolvedValue({
      enabled: true,
      mode: 'toggle',
      model_id: 'sherpa-parakeet-tdt-v2',
    });
    useAppStore
      .getState()
      .setDictationModelId('csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8');
    await flush();
    expect(useAppStore.getState().dictationModelId).toBe('sherpa-parakeet-tdt-v2');
  });

  it('rolls back the optimistic value when the write fails', async () => {
    apiPost.mockRejectedValue(new Error('boom'));
    useAppStore.getState().setDictationEnabled(false);
    expect(useAppStore.getState().dictationEnabled).toBe(false); // optimistic
    await flush();
    expect(useAppStore.getState().dictationEnabled).toBe(true); // rolled back
  });
});
