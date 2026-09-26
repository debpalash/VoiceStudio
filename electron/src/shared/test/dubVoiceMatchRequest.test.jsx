import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAppStore } from '../store';

// Voice-match toggle — store → /dub/generate request wiring (mirrors the
// timing_strategy pattern). `per_line` is the DEFAULT and must be what a
// fresh store sends (the backend treats it as the unchanged Wave 3.2
// behaviour); flipping the store to `consistent` must reach the request
// body, and the incremental recompute must send the same mode so the
// fingerprints stay in parity (#281 class).

const dubApi = vi.hoisted(() => ({
  dubUpload: vi.fn(),
  dubIngestUrl: vi.fn(),
  dubAbort: vi.fn(),
  dubCleanupSegments: vi.fn(),
  dubTranslate: vi.fn(),
  dubGenerate: vi.fn(),
  tasksStreamUrl: vi.fn(() => ''),
  tasksCancel: vi.fn(),
  transcribeStreamUrl: vi.fn(() => ''),
  dubImportSrt: vi.fn(),
}));
vi.mock('../api/dub', () => dubApi);
const clientApi = vi.hoisted(() => ({
  apiPost: vi.fn(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
  API: '',
}));
vi.mock('../api/client', () => clientApi);

import useDubWorkflow from '../hooks/useDubWorkflow';
import useSegmentEditing from '../hooks/useSegmentEditing';

const baseState = useAppStore.getState();

function renderWorkflow() {
  return renderHook(() =>
    useDubWorkflow({
      loadProjects: vi.fn(),
      loadProfiles: vi.fn(),
      loadDubHistory: vi.fn(),
      setLastGenFingerprints: vi.fn(),
    }),
  );
}

/** An SSE stream that ends immediately: handleDubGenerate still posts the
 * generate request (the part under test), then surfaces a stream-ended error
 * through the store — no timers, no dangling reads. */
function endOfStream() {
  return {
    body: { getReader: () => ({ read: async () => ({ done: true }) }) },
  };
}

beforeEach(() => {
  useAppStore.setState(baseState, true);
  dubApi.dubGenerate.mockReset().mockResolvedValue({ task_id: 't1' });
  clientApi.apiFetch.mockReset().mockResolvedValue(endOfStream());
  clientApi.apiPost.mockReset();
  useAppStore.setState({
    dubJobId: 'job1',
    dubStep: 'editing',
    dubLangCode: 'es',
    dubSegments: [{ id: '1', text: 'hola', text_original: 'hello', start: 0, end: 2 }],
  });
});

describe('voice_match — generate request wiring', () => {
  it('a fresh store sends the DEFAULT per_line', async () => {
    const { result } = renderWorkflow();
    await act(async () => {
      await result.current.handleDubGenerate();
    });
    expect(dubApi.dubGenerate).toHaveBeenCalledWith(
      'job1',
      expect.objectContaining({ voice_match: 'per_line' }),
    );
  });

  it('flipping the store to consistent reaches the request body', async () => {
    useAppStore.setState({ voiceMatch: 'consistent' });
    const { result } = renderWorkflow();
    await act(async () => {
      await result.current.handleDubGenerate();
    });
    expect(dubApi.dubGenerate).toHaveBeenCalledWith(
      'job1',
      expect.objectContaining({ voice_match: 'consistent' }),
    );
  });

  it('setVoiceMatch is the store setter the Segmented control drives', () => {
    expect(useAppStore.getState().voiceMatch).toBe('per_line');
    act(() => useAppStore.getState().setVoiceMatch('consistent'));
    expect(useAppStore.getState().voiceMatch).toBe('consistent');
  });
});

describe('voice_match — incremental recompute parity (#281 class)', () => {
  it('recomputeIncremental sends the store mode alongside lang + hashes', async () => {
    useAppStore.setState({ voiceMatch: 'consistent' });
    clientApi.apiPost.mockResolvedValue({ stale: ['1'], fresh: [], fingerprints: {} });
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.setLastGenFingerprints({ 1: 'hash-es' }, 'es'));
    await act(async () => {
      await result.current.recomputeIncremental();
    });
    expect(clientApi.apiPost).toHaveBeenCalledWith(
      '/tools/incremental',
      expect.objectContaining({ lang: 'es', voice_match: 'consistent' }),
    );
  });
});
