import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAppStore } from '../store';

// P1.1 — `handleTranslateAll(langOverride?)`:
//  - no-arg keeps the existing Translate All behavior (target = store's
//    dubLangCode),
//  - a string override translates INTO that language (the multi-language
//    generate loop passes each pick's code),
//  - a non-string first arg (the `onClick={handleTranslateAll}` click event)
//    must be ignored, not treated as a language,
//  - resolves true only when a translation actually landed — the batch loop
//    keys "skip this language's generate" off that.

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
vi.mock('../api/client', () => ({
  apiPost: vi.fn(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
  API: '',
}));

import useDubWorkflow from '../hooks/useDubWorkflow';

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

describe('handleTranslateAll(langOverride) — multi-language target override', () => {
  beforeEach(() => {
    useAppStore.setState(baseState, true);
    dubApi.dubTranslate.mockReset();
    useAppStore.setState({
      dubJobId: 'job1',
      dubStep: 'editing',
      dubLangCode: 'es',
      dubSegments: [
        { id: '1', text: 'hello there', text_original: 'hello there', start: 0, end: 2 },
      ],
    });
  });

  it('no-arg behavior unchanged: translates into the store dubLangCode and applies the text', async () => {
    dubApi.dubTranslate.mockResolvedValue({
      translated: [{ id: '1', text: 'hola' }],
      target_lang: 'es',
    });
    const { result } = renderWorkflow();
    let ok;
    await act(async () => {
      ok = await result.current.handleTranslateAll();
    });
    expect(dubApi.dubTranslate).toHaveBeenCalledTimes(1);
    expect(dubApi.dubTranslate.mock.calls[0][0].target_lang).toBe('es');
    expect(ok).toBe(true);
    expect(useAppStore.getState().dubSegments[0].text).toBe('hola');
  });

  it('string override translates INTO the override language, not the store selection', async () => {
    dubApi.dubTranslate.mockResolvedValue({
      translated: [{ id: '1', text: 'ওহে' }],
      target_lang: 'bn',
    });
    const { result } = renderWorkflow();
    let ok;
    await act(async () => {
      ok = await result.current.handleTranslateAll('bn');
    });
    expect(dubApi.dubTranslate.mock.calls[0][0].target_lang).toBe('bn');
    expect(ok).toBe(true);
    expect(useAppStore.getState().dubSegments[0].text).toBe('ওহে');
  });

  it('a click event as first arg (onClick={handleTranslateAll}) falls back to dubLangCode', async () => {
    dubApi.dubTranslate.mockResolvedValue({
      translated: [{ id: '1', text: 'hola' }],
      target_lang: 'es',
    });
    const { result } = renderWorkflow();
    await act(async () => {
      await result.current.handleTranslateAll({ preventDefault() {}, type: 'click' });
    });
    expect(dubApi.dubTranslate.mock.calls[0][0].target_lang).toBe('es');
  });

  it('sends the explicitly selected spoken language', async () => {
    useAppStore.setState({ dubSourceLangCode: 'fr' });
    dubApi.dubTranslate.mockResolvedValue({
      translated: [{ id: '1', text: 'hola' }],
      target_lang: 'es',
    });
    const { result } = renderWorkflow();

    await act(async () => {
      await result.current.handleTranslateAll();
    });

    expect(dubApi.dubTranslate.mock.calls[0][0].source_lang).toBe('fr');
  });

  it('retries only segments carrying a translation error', async () => {
    useAppStore.setState({
      dubSegments: [
        { id: '1', text: 'ok', text_original: 'ok', start: 0, end: 1 },
        {
          id: '2',
          text: 'failed',
          text_original: 'failed',
          start: 1,
          end: 2,
          translate_error: 'offline',
        },
      ],
    });
    dubApi.dubTranslate.mockResolvedValue({
      translated: [{ id: '2', text: 'recuperado' }],
      target_lang: 'es',
    });
    const { result } = renderWorkflow();

    await act(async () => {
      await result.current.handleTranslateAll({ retryFailed: true });
    });

    expect(dubApi.dubTranslate.mock.calls[0][0].segments.map((segment) => segment.id)).toEqual([
      '2',
    ]);
    expect(useAppStore.getState().dubSegments[1].translate_error).toBeUndefined();
  });

  it('retries and clears failures by target language', async () => {
    useAppStore.setState({
      dubSegments: [
        {
          id: '1',
          text: 'failed twice',
          text_original: 'failed twice',
          start: 0,
          end: 1,
        },
        {
          id: '2',
          text: 'failed in French',
          text_original: 'failed in French',
          start: 1,
          end: 2,
        },
      ],
    });
    dubApi.dubTranslate
      .mockResolvedValueOnce({
        translated: [
          { id: '1', text: '', error: 'es offline' },
          { id: '2', text: 'es:2' },
        ],
        target_lang: 'es',
      })
      .mockResolvedValueOnce({
        translated: [
          { id: '1', text: '', error: 'fr offline' },
          { id: '2', text: '', error: 'fr offline' },
        ],
        target_lang: 'fr',
      });
    const { result } = renderWorkflow();

    await act(async () => {
      await result.current.handleTranslateAll('es');
      await result.current.handleTranslateAll('fr');
    });
    expect(useAppStore.getState().dubSegments.map((segment) => segment.translate_errors)).toEqual([
      { es: 'es offline', fr: 'fr offline' },
      { fr: 'fr offline' },
    ]);

    dubApi.dubTranslate.mockImplementation(async ({ segments, target_lang: targetLang }) => ({
      translated: segments.map(({ id }) => ({ id, text: `${targetLang}:${id}` })),
      target_lang: targetLang,
    }));

    await act(async () => {
      await result.current.handleTranslateAll({ retryFailed: true, langOverride: 'es' });
    });
    expect(dubApi.dubTranslate.mock.calls[2][0].segments.map(({ id }) => id)).toEqual(['1']);
    expect(useAppStore.getState().dubSegments[0].translate_errors).toEqual({
      fr: 'fr offline',
    });

    await act(async () => {
      await result.current.handleTranslateAll({ retryFailed: true, langOverride: 'fr' });
    });
    expect(dubApi.dubTranslate.mock.calls[3][0].segments.map(({ id }) => id)).toEqual(['1', '2']);
    expect(useAppStore.getState().dubSegments.map((segment) => segment.translate_errors)).toEqual([
      undefined,
      undefined,
    ]);
  });

  it('request failure resolves false and surfaces the existing error banner', async () => {
    dubApi.dubTranslate.mockRejectedValue(new Error('engine down'));
    const { result } = renderWorkflow();
    let ok;
    await act(async () => {
      ok = await result.current.handleTranslateAll('bn');
    });
    expect(ok).toBe(false);
    expect(useAppStore.getState().dubError).toMatch(/engine down/);
    expect(useAppStore.getState().isTranslating).toBe(false);
  });

  it('an all-errors result resolves false (nothing translated → no wrong-language dub)', async () => {
    dubApi.dubTranslate.mockResolvedValue({
      translated: [{ id: '1', text: '', error: 'boom' }],
      target_lang: 'bn',
    });
    const { result } = renderWorkflow();
    let ok;
    await act(async () => {
      ok = await result.current.handleTranslateAll('bn');
    });
    expect(ok).toBe(false);
  });

  it('reads segments from the store at call time (stale click-time closure is the loop bug class)', async () => {
    dubApi.dubTranslate.mockResolvedValue({
      translated: [{ id: '2', text: 'nuevo' }],
      target_lang: 'es',
    });
    const { result } = renderWorkflow();
    const stale = result.current.handleTranslateAll; // captured before the segments change
    act(() => {
      useAppStore
        .getState()
        .setDubSegments([{ id: '2', text: 'fresh', text_original: 'fresh', start: 0, end: 1 }]);
    });
    await act(async () => {
      await stale();
    });
    const sent = dubApi.dubTranslate.mock.calls[0][0].segments;
    expect(sent).toHaveLength(1);
    expect(sent[0].id).toBe('2');
    expect(sent[0].text).toBe('fresh');
  });
});
