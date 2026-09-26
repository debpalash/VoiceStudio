import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, fireEvent, screen } from '@testing-library/react';
import toast from 'react-hot-toast';
import { useAppStore } from '../store';

// P1.1 — the multi-language generate loop must TRANSLATE each pick before it
// generates it. Pre-fix the loop only called handleDubGenerate per language,
// so "Generate 3 dubs" synthesized the same (untranslated) text three times —
// at most one track was actually in its language.
//
// Contract under test (call order, per pick):
//   translate(code) → generate({ langOverride: { language, language_code } })
// and on a failed translate: skip that pick's generate, keep going, report
// the skipped languages in a final toast.

const captured = vi.hoisted(() => ({ header: [], left: [] }));
vi.mock('../components/dub/DubHeader', () => ({
  default: (props) => {
    captured.header.push(props);
    return null;
  },
}));
vi.mock('../components/dub/DubLeftColumn', () => ({
  default: (props) => {
    captured.left.push(props);
    return null;
  },
}));
vi.mock('../components/dub/DubRightColumn', () => ({ default: () => null }));
vi.mock('../components/dub/DubFooter', () => ({ default: () => null }));
vi.mock('../components/dub/DubPipelineStepper', () => ({ default: () => null }));
vi.mock('../components/dub/IdleSkeleton', () => ({ default: () => null }));
vi.mock('../hooks/useTimelineOnsets', () => ({ default: () => ({ onsets: [] }) }));
vi.mock('../api/dub', () => ({ dubQc: vi.fn() }));
// Never-resolving async deps keep the render synchronous (no post-test act noise).
vi.mock('../api/engines', () => ({
  listTranslationEngines: vi.fn(() => new Promise(() => {})),
  installTranslationEngine: vi.fn(),
}));
vi.mock('../api/client', async (importOriginal) => {
  const mod = await importOriginal();
  return { ...mod, apiJson: vi.fn(() => new Promise(() => {})) };
});

import DubTab from '../pages/DubTab';
import ExportModal from '../components/ExportModal';

const noop = () => {};
function makeProps(over = {}) {
  return {
    dubVideoFile: null,
    dubLocalBlobUrl: null,
    transcribeElapsed: 0,
    translateProvider: 'google',
    setTranslateProvider: noop,
    showTranscript: false,
    setShowTranscript: noop,
    onGlossaryChange: noop,
    profiles: [],
    segmentPreviewLoading: null,
    selectedSegIds: new Set(),
    setDubVideoFile: noop,
    setDubLocalBlobUrl: noop,
    handleDubAbort: noop,
    handleDubUpload: noop,
    handleDubIngestUrl: noop,
    handleDubRetryTranscribe: noop,
    handleDubStop: noop,
    handleDubGenerate: noop,
    handleDubImportSrt: noop,
    handleDubDownload: noop,
    handleDubAudioDownload: noop,
    handleAudioExport: noop,
    handleSegmentPreview: noop,
    onDirectSegment: noop,
    handleTranslateAll: noop,
    handleCleanupSegments: noop,
    incrementalPlan: null,
    triggerDownload: noop,
    fileToMediaUrl: noop,
    editSegments: noop,
    saveProject: noop,
    resetDub: noop,
    segmentEditField: noop,
    segmentDelete: noop,
    segmentRestoreOriginal: noop,
    segmentSplit: noop,
    segmentMerge: noop,
    segmentMoveResize: noop,
    timelineSelSegId: null,
    setTimelineSelSegId: noop,
    toggleSegSelect: noop,
    selectAllSegs: noop,
    clearSegSelection: noop,
    bulkApplyToSelected: noop,
    bulkDeleteSelected: noop,
    ...over,
  };
}

const baseState = useAppStore.getState();
const PICKS = [
  { lang: 'Hindi', code: 'hi' },
  { lang: 'Spanish', code: 'es' },
  { lang: 'French', code: 'fr' },
  { lang: 'German', code: 'de' },
];
const EXPECTED_CODES = ['bn', ...PICKS.map((pick) => pick.code)];

/** Render DubTab in multi-lang mode and return { onGenerateClick, calls, mocks }. */
function setup({
  translateOk = () => true,
  translatedText = (code, segment) => `${code}:${segment.text_original || segment.text}`,
  langCode = 'bn',
  segments,
} = {}) {
  const calls = [];
  const handleTranslateAll = vi.fn(async (arg) => {
    const code = typeof arg === 'string' ? arg : arg?.langOverride;
    calls.push(`translate:${code}`);
    const ok = translateOk(code);
    if (ok) {
      useAppStore.getState().setDubSegments((prev) =>
        prev.map((segment, index) => {
          const text = translatedText(code, segment, index);
          return text
            ? { ...segment, text, translations: { ...segment.translations, [code]: text } }
            : segment;
        }),
      );
    }
    return ok;
  });
  const handleDubGenerate = vi.fn(async (opts) => {
    const code = opts?.langOverride?.language_code ?? 'default';
    calls.push(`generate:${code}`);
    if (code !== 'default') {
      useAppStore.getState().setDubTracks((prev) => (prev.includes(code) ? prev : [...prev, code]));
    }
  });
  useAppStore.setState({
    dubJobId: 'job1',
    dubStep: 'editing',
    dubLangCode: langCode,
    dubLang: langCode === 'bn' ? 'Bengali' : 'English',
    multiLangMode: true,
    multiLangs: PICKS,
    dubTracks: [],
    dubSegments: segments ?? [{ id: '1', text: 'hello', text_original: 'hello' }],
  });
  render(<DubTab {...makeProps({ handleTranslateAll, handleDubGenerate })} />);
  return {
    onGenerateClick: captured.header.at(-1).onGenerateClick,
    calls,
    handleTranslateAll,
    handleDubGenerate,
  };
}

describe('DubTab — multi-language generate translates each language first (P1.1)', () => {
  beforeEach(() => {
    useAppStore.setState(baseState, true);
    captured.header.length = 0;
    captured.left.length = 0;
  });

  it('forwards retry-only options through every language in the wrapper', async () => {
    const { handleTranslateAll } = setup();

    await act(async () => {
      await captured.left.at(-1).handleTranslateAll({ retryFailed: true });
    });

    expect(handleTranslateAll.mock.calls.map(([options]) => options)).toEqual(
      EXPECTED_CODES.map((langOverride) => ({ retryFailed: true, langOverride })),
    );
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("picks ['bn','es']: each language's translate runs BEFORE its generate, in order", async () => {
    const { onGenerateClick, calls, handleDubGenerate } = setup();
    await act(async () => {
      await onGenerateClick();
    });
    // Pre-fix this was ['generate:bn', 'generate:es'] — translate never ran.
    expect(calls).toEqual([
      'translate:bn',
      'generate:bn',
      'translate:hi',
      'generate:hi',
      'translate:es',
      'generate:es',
      'translate:fr',
      'generate:fr',
      'translate:de',
      'generate:de',
    ]);
    // langOverride keeps the existing handleDubGenerate call shape.
    expect(handleDubGenerate).toHaveBeenNthCalledWith(1, {
      langOverride: { language: 'Bengali', language_code: 'bn' },
    });
    expect(handleDubGenerate).toHaveBeenNthCalledWith(2, {
      langOverride: { language: 'Hindi', language_code: 'hi' },
    });
    const state = useAppStore.getState();
    expect(state.dubTracks).toEqual(['bn', 'hi', 'es', 'fr', 'de']);
    expect(state.dubSegments[0].translations).toEqual({
      bn: 'bn:hello',
      hi: 'hi:hello',
      es: 'es:hello',
      fr: 'fr:hello',
      de: 'de:hello',
    });

    // The generated per-language track state feeds the export drawer, where
    // both tracks are selected by default and handed to the export action.
    const exported = [];
    render(
      <ExportModal
        open
        onClose={noop}
        jobId="job1"
        filename="video.mp4"
        dubTracks={state.dubTracks}
        dubLangCode="es"
        preserveBg={false}
        setPreserveBg={noop}
        defaultTrack="original"
        setDefaultTrack={noop}
        exportTracks={{}}
        setExportTracks={noop}
        dualSubs={false}
        setDualSubs={noop}
        burnSubs={false}
        setBurnSubs={noop}
        API=""
        triggerDownload={noop}
        handleDubDownload={() => exported.push(...useAppStore.getState().dubTracks)}
        handleAudioExport={noop}
        segmentCount={1}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /tracks 6\/6/i }));
    expect(screen.getByLabelText(/^BN/)).toBeChecked();
    expect(screen.getByLabelText(/^HI/)).toBeChecked();
    expect(screen.getByLabelText(/^ES/)).toBeChecked();
    expect(screen.getByLabelText(/^FR/)).toBeChecked();
    expect(screen.getByLabelText(/^DE/)).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Export MP4' }));
    expect(exported).toEqual(['bn', 'hi', 'es', 'fr', 'de']);
  });

  it('a failed translate skips ONLY that language’s generate, continues, and reports it', async () => {
    const errorSpy = vi.spyOn(toast, 'error');
    const { onGenerateClick, calls } = setup({ translateOk: (code) => code !== 'bn' });
    await act(async () => {
      await onGenerateClick();
    });
    expect(calls).toEqual([
      'translate:bn',
      'translate:hi',
      'generate:hi',
      'translate:es',
      'generate:es',
      'translate:fr',
      'generate:fr',
      'translate:de',
      'generate:de',
    ]);
    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy.mock.calls[0][0]).toContain('Bengali');
  });

  it('skips the redundant translate only when the FIRST pick already matches freshly-translated editor text', async () => {
    const { onGenerateClick, calls } = setup({
      langCode: 'bn',
      // text differs from text_original on every segment = a translation into
      // dubLangCode ('bn') is already applied — pick 1 can go straight to generate.
      segments: [
        {
          id: '1',
          text: 'ওহে',
          text_original: 'hello',
          translations: { bn: 'ওহে' },
        },
      ],
    });
    await act(async () => {
      await onGenerateClick();
    });
    expect(calls).toEqual([
      'generate:bn',
      'translate:hi',
      'generate:hi',
      'translate:es',
      'generate:es',
      'translate:fr',
      'generate:fr',
      'translate:de',
      'generate:de',
    ]);
  });

  it('changed text from another language never suppresses the selected target translation', async () => {
    const { onGenerateClick, calls } = setup({
      langCode: 'bn',
      segments: [
        {
          id: '1',
          text: 'hola',
          text_original: 'hello',
          translations: { es: 'hola' },
        },
      ],
    });
    await act(async () => {
      await onGenerateClick();
    });
    expect(calls).toEqual([
      'translate:bn',
      'generate:bn',
      'translate:hi',
      'generate:hi',
      'generate:es',
      'translate:fr',
      'generate:fr',
      'translate:de',
      'generate:de',
    ]);
  });

  it('a partial translation never generates a mixed-language track and later targets continue', async () => {
    const errorSpy = vi.spyOn(toast, 'error');
    const { onGenerateClick, calls } = setup({
      segments: [
        { id: '1', text: 'one', text_original: 'one' },
        { id: '2', text: 'two', text_original: 'two' },
      ],
      translatedText: (code, segment, index) =>
        code === 'bn' && index === 1 ? null : `${code}:${segment.text_original}`,
    });
    await act(async () => {
      await onGenerateClick();
    });
    expect(calls).toEqual([
      'translate:bn',
      'translate:hi',
      'generate:hi',
      'translate:es',
      'generate:es',
      'translate:fr',
      'generate:fr',
      'translate:de',
      'generate:de',
    ]);
    expect(useAppStore.getState().dubTracks).toEqual(['hi', 'es', 'fr', 'de']);
    expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining('Bengali'), {
      duration: 8000,
    });
  });

  it('untranslated editor text is ALWAYS translated, even when the first pick matches dubLangCode', async () => {
    const { onGenerateClick, calls } = setup({
      langCode: 'bn',
      segments: [{ id: '1', text: 'hello', text_original: 'hello' }],
    });
    await act(async () => {
      await onGenerateClick();
    });
    expect(calls).toEqual([
      'translate:bn',
      'generate:bn',
      'translate:hi',
      'generate:hi',
      'translate:es',
      'generate:es',
      'translate:fr',
      'generate:fr',
      'translate:de',
      'generate:de',
    ]);
  });

  it('single-language mode is untouched: generate only, no translate, no override', async () => {
    const { onGenerateClick, calls, handleDubGenerate, handleTranslateAll } = setup();
    act(() => {
      useAppStore.setState({ multiLangMode: false });
    });
    void onGenerateClick; // stale capture — re-read after the mode flip
    const fresh = captured.header.at(-1).onGenerateClick;
    await act(async () => {
      await fresh();
    });
    expect(handleTranslateAll).not.toHaveBeenCalled();
    expect(handleDubGenerate).toHaveBeenCalledWith();
    expect(calls).toEqual(['generate:default']);
  });
});
