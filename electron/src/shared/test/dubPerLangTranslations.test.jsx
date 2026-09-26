import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAppStore } from '../store';

// P1.2 / P1.3 — per-language translation storage + per-track fingerprints.
//
// `dubSegments[].text` is single-slot (the currently-shown language); before
// this fix, switching the dub target language left the previous language's
// text on screen and the next Translate All DESTROYED it. Now every
// translation is kept in `s.translations[langCode]`, `switchDubLangCode`
// swaps `text` through that map non-destructively, manual edits update the
// current language's entry, and the incremental-plan fingerprints are stored
// per language so "Regen N changed" judges the ACTIVE track.

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

const seg = (over = {}) => ({
  id: '1',
  text: 'hello there',
  text_original: 'hello there',
  start: 0,
  end: 2,
  ...over,
});

beforeEach(() => {
  useAppStore.setState(baseState, true);
  dubApi.dubTranslate.mockReset();
  clientApi.apiPost.mockReset();
  useAppStore.setState({
    dubJobId: 'job1',
    dubStep: 'editing',
    dubLangCode: 'bn',
    dubSegments: [seg()],
  });
});

const translateTo = async (result, lang, text) => {
  dubApi.dubTranslate.mockResolvedValueOnce({
    translated: [{ id: '1', text }],
    target_lang: lang,
  });
  await act(async () => {
    await result.current.handleTranslateAll(lang);
  });
};

describe('per-language translations (P1.2)', () => {
  it('translate bn then es retains BOTH languages in s.translations (pre-fix: bn lost)', async () => {
    const { result } = renderWorkflow();
    await translateTo(result, 'bn', 'ওহে');
    act(() => useAppStore.getState().switchDubLangCode('es'));
    await translateTo(result, 'es', 'hola');

    const s = useAppStore.getState().dubSegments[0];
    expect(s.text).toBe('hola'); // text stays the shown language (legacy slot)
    expect(s.translations).toMatchObject({ bn: 'ওহে', es: 'hola' });
  });

  it('switching the target language swaps text non-destructively, both directions', async () => {
    const { result } = renderWorkflow();
    await translateTo(result, 'bn', 'ওহে');
    act(() => useAppStore.getState().switchDubLangCode('es'));
    await translateTo(result, 'es', 'hola');

    act(() => useAppStore.getState().switchDubLangCode('bn'));
    expect(useAppStore.getState().dubSegments[0].text).toBe('ওহে');
    act(() => useAppStore.getState().switchDubLangCode('es'));
    expect(useAppStore.getState().dubSegments[0].text).toBe('hola');
  });

  it('language hydration invalidates merge offsets from the previous text', () => {
    useAppStore.setState({
      dubSegments: [
        seg({
          translations: { es: 'hola' },
          merge_parts: [{ textStart: 0, textEnd: 11, speaker_id: 'Anna' }],
        }),
      ],
    });

    act(() => useAppStore.getState().switchDubLangCode('es'));

    expect(useAppStore.getState().dubSegments[0].merge_parts).toBeUndefined();
  });

  it('a fresh translation invalidates merge offsets from the source text', async () => {
    useAppStore.setState({
      dubSegments: [seg({ merge_parts: [{ textStart: 0, textEnd: 11, speaker_id: 'Anna' }] })],
    });
    const { result } = renderWorkflow();

    await translateTo(result, 'bn', 'ওহে');

    expect(useAppStore.getState().dubSegments[0].merge_parts).toBeUndefined();
  });

  it('switching to a never-translated language leaves text unchanged (legacy behaviour)', () => {
    useAppStore.setState({
      dubSegments: [seg({ text: 'ওহে', translations: { bn: 'ওহে' } })],
    });
    act(() => useAppStore.getState().switchDubLangCode('es'));
    // Non-destructive: no es entry → keep showing what was there.
    expect(useAppStore.getState().dubSegments[0].text).toBe('ওহে');
    expect(useAppStore.getState().dubLangCode).toBe('es');
  });

  it('legacy segments (no translations field) survive a switch round-trip', () => {
    // A pre-upgrade project where bn text was already translated in place.
    useAppStore.setState({
      dubSegments: [seg({ text: 'ওহে' })], // text !== text_original, no map
    });
    act(() => useAppStore.getState().switchDubLangCode('es'));
    act(() => useAppStore.getState().switchDubLangCode('bn'));
    // The switch snapshotted bn's text into the map instead of losing it.
    expect(useAppStore.getState().dubSegments[0].text).toBe('ওহে');
    expect(useAppStore.getState().dubSegments[0].translations.bn).toBe('ওহে');
  });

  it('never stamps untranslated (source) text as a translation on switch', () => {
    // text === text_original → not a translation, must not be snapshotted.
    act(() => useAppStore.getState().switchDubLangCode('es'));
    expect(useAppStore.getState().dubSegments[0].translations.bn).toBeUndefined();
  });

  it('manual segment edit updates the CURRENT language entry only', () => {
    useAppStore.setState({
      dubLangCode: 'es',
      dubSegments: [seg({ text: 'hola', translations: { bn: 'ওহে', es: 'hola' } })],
    });
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.segmentEditField('1', 'text', 'hola editada'));
    const s = useAppStore.getState().dubSegments[0];
    expect(s.text).toBe('hola editada');
    expect(s.translations).toEqual({ bn: 'ওহে', es: 'hola editada' });
  });

  it('restore-original records the decision under the current language', () => {
    useAppStore.setState({
      dubLangCode: 'es',
      dubSegments: [seg({ text: 'hola', translations: { es: 'hola' } })],
    });
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.segmentRestoreOriginal('1'));
    const s = useAppStore.getState().dubSegments[0];
    expect(s.text).toBe('hello there');
    expect(s.translations.es).toBe('hello there');
  });

  it('merge joins per-language texts only where both rows carry the language', () => {
    useAppStore.setState({
      dubLangCode: 'es',
      dubSegments: [
        seg({ id: 'a', end: 1, translations: { es: 'uno', bn: 'এক' } }),
        seg({ id: 'b', start: 1, translations: { es: 'dos' } }),
      ],
    });
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.segmentMerge('a'));
    const merged = useAppStore.getState().dubSegments[0];
    expect(merged.translations).toEqual({ es: 'uno dos' }); // bn half-known → dropped
  });

  it('split drops the per-language map (new ids need fresh translations)', () => {
    useAppStore.setState({
      dubLangCode: 'es',
      dubSegments: [seg({ text: 'hola mundo', translations: { es: 'hola mundo' } })],
    });
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.segmentSplit('1', 5));
    const segs = useAppStore.getState().dubSegments;
    expect(segs).toHaveLength(2);
    expect(segs[0].translations).toBeUndefined();
    expect(segs[1].translations).toBeUndefined();
  });
});

describe('per-track fingerprints (P1.3)', () => {
  it('lastGenFingerprints follows the ACTIVE language', () => {
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.setLastGenFingerprints({ 1: 'hash-bn' }, 'bn'));
    act(() => result.current.setLastGenFingerprints({ 1: 'hash-es' }, 'es'));
    expect(useAppStore.getState().dubLangCode).toBe('bn');
    expect(result.current.lastGenFingerprints).toEqual({ 1: 'hash-bn' });
    act(() => useAppStore.getState().switchDubLangCode('es'));
    expect(result.current.lastGenFingerprints).toEqual({ 1: 'hash-es' });
  });

  it('recomputeIncremental sends the active lang + that language’s hashes', async () => {
    clientApi.apiPost.mockResolvedValue({ stale: [], fresh: ['1'], fingerprints: {} });
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.setLastGenFingerprints({ 1: 'hash-bn' }, 'bn'));
    await act(async () => {
      await result.current.recomputeIncremental();
    });
    expect(clientApi.apiPost).toHaveBeenCalledWith(
      '/tools/incremental',
      expect.objectContaining({ lang: 'bn', stored_hashes: { 1: 'hash-bn' } }),
    );
    expect(result.current.incrementalPlan).toEqual({ stale: [], fresh: ['1'] });
  });

  it('a language with no stored hashes yields no plan (unknowable ≠ stale)', async () => {
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.setLastGenFingerprints({ 1: 'hash-es' }, 'es'));
    // Active language is bn → no hashes → plan cleared, no API call.
    await act(async () => {
      await result.current.recomputeIncremental();
    });
    expect(clientApi.apiPost).not.toHaveBeenCalled();
    expect(result.current.incrementalPlan).toBeNull();
  });

  it('setFingerprintsByLang restores every track at once (project/history load)', () => {
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.setFingerprintsByLang({ bn: { 1: 'hb' }, es: { 1: 'he' } }));
    expect(result.current.lastGenFingerprints).toEqual({ 1: 'hb' }); // active = bn
    expect(result.current.fingerprintsByLang.es).toEqual({ 1: 'he' });
  });

  it('setLastGenFingerprints without a lang defaults to the store selection', () => {
    const { result } = renderHook(() => useSegmentEditing());
    act(() => result.current.setLastGenFingerprints({ 1: 'h' }));
    expect(result.current.fingerprintsByLang).toEqual({ bn: { 1: 'h' } });
  });
});
