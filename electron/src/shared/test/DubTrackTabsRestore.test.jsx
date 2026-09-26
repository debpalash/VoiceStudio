import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import toast from 'react-hot-toast';
import { useAppStore } from '../store';

// Regression guard for the "completed dub tracks' tabs hidden until the
// language is re-selected" P0:
//  - `hasDubbedTrack` must key off the persisted tracks ONLY. The old
//    expression required `dubLangCode !== 'und'` and ended in a tautology
//    (`dubTracks?.length > 0 || !!dubTracks`), so a restored project with
//    finished tracks but a frozen language_code ('und') hid the track
//    switcher — and a job with NO tracks showed it.
//  - The done-state auto-jump must be membership-guarded: jumping the preview
//    to a dubLangCode that has no track (restores fall back to 'en' with
//    tracks ['bn']) pointed the player at /dub/preview-video?lang=en → 404.

// Heavy children are stubbed; DubLeftColumn is the probe — DubTab owns both
// `hasDubbedTrack` and the previewMode auto-jump, and hands them down as props.
const captured = vi.hoisted(() => ({ left: [], header: [] }));
vi.mock('../components/dub/DubLeftColumn', () => ({
  default: (props) => {
    captured.left.push(props);
    return <div data-testid="left-col" />;
  },
}));
vi.mock('../components/dub/DubHeader', () => ({
  default: (props) => {
    captured.header.push(props);
    const { resetDub, pipelineSteps = [], onPipelineStep } = props;
    return (
      <div>
        <button data-testid="reset-dub" onClick={resetDub}>
          reset
        </button>
        {pipelineSteps.map((step) => (
          <button key={step} onClick={() => onPipelineStep(step)}>
            {step}
          </button>
        ))}
      </div>
    );
  },
}));
vi.mock('../components/dub/DubRightColumn', () => ({ default: () => null }));
vi.mock('../components/dub/DubFooter', () => ({ default: () => null }));
vi.mock('../components/dub/DubPipelineStepper', () => ({
  default: ({ selectableSteps = [], onStepSelect }) => (
    <div>
      {selectableSteps.map((step) => (
        <button key={step} onClick={() => onStepSelect(step)}>
          {step}
        </button>
      ))}
    </div>
  ),
}));
vi.mock('../components/dub/IdleSkeleton', () => ({
  default: ({ youtubeCookieFile, setYoutubeCookieFile }) => (
    <div>
      <span data-testid="cookie-name">{youtubeCookieFile?.name || 'none'}</span>
      <button
        data-testid="select-cookie"
        onClick={() => setYoutubeCookieFile(new File(['secret'], 'cookies.txt'))}
      >
        select
      </button>
    </div>
  ),
}));
vi.mock('../components/ExportModal', () => ({ default: () => null }));
vi.mock('../hooks/useTimelineOnsets', () => ({ default: () => ({ onsets: [] }) }));
vi.mock('../api/dub', () => ({
  dubQc: vi.fn(),
  dubListTracks: vi.fn(() => new Promise(() => {})),
}));
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
import { dubQc } from '../api/dub';

const noop = () => {};
function makeProps() {
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
  };
}

const baseState = useAppStore.getState();

function renderDone({ tracks, langCode, lang = 'Auto' }) {
  useAppStore.setState({
    dubJobId: 'job1',
    dubStep: 'done',
    dubTracks: tracks,
    dubLangCode: langCode,
    dubLang: lang,
  });
  render(<DubTab {...makeProps()} />);
  return captured.left.at(-1);
}

describe('DubTab — completed tracks always show their tabs (restore P0)', () => {
  beforeEach(() => {
    useAppStore.setState(baseState, true);
    captured.left.length = 0;
    captured.header.length = 0;
    vi.mocked(dubQc).mockReset();
  });

  it("restored project (tracks ['bn'], language_code frozen at 'und'): switcher shows and preview jumps to the track", () => {
    const left = renderDone({ tracks: ['bn'], langCode: 'und' });
    // Pre-fix: `dubLangCode !== 'und'` hid the finished tracks' tabs.
    expect(left.hasDubbedTrack).toBe(true);
    // Auto-jump falls back to the only real track — never a lang without one.
    expect(left.previewMode).toBe('bn');
    expect(left.playbackAudioSrc).toContain('/dub/download-audio/job1?lang=bn');
  });

  it("membership guard: dubLangCode 'en' with tracks ['bn'] previews tracks[0], not the 404 lang", () => {
    const left = renderDone({ tracks: ['bn'], langCode: 'en', lang: 'English' });
    expect(left.hasDubbedTrack).toBe(true);
    // Pre-fix the auto-jump previewed 'en' → /dub/preview-video?lang=en → 404.
    expect(left.previewMode).toBe('bn');
  });

  it('dubLangCode that has a track previews that track (fresh-generate path unchanged)', () => {
    const left = renderDone({ tracks: ['bn', 'es'], langCode: 'es', lang: 'Spanish' });
    expect(left.hasDubbedTrack).toBe(true);
    expect(left.previewMode).toBe('es');
  });

  it('done with NO persisted tracks hides the switcher and stays on Original (tautology guard)', () => {
    const left = renderDone({ tracks: [], langCode: 'es', lang: 'Spanish' });
    // Pre-fix `(dubTracks?.length > 0 || !!dubTracks)` was always true, so the
    // switcher appeared trackless and the auto-jump 404'd the preview.
    expect(left.hasDubbedTrack).toBe(false);
    expect(left.previewMode).toBe('original');
    expect(left.playbackAudioSrc).toContain('/dub/audio/job1');
  });

  it('clears a selected cookie export when a completed dub is reset', () => {
    const resetDub = vi.fn(() =>
      useAppStore.setState({ dubJobId: null, dubStep: 'idle', dubTracks: [] }),
    );
    useAppStore.setState({ dubJobId: null, dubStep: 'idle', dubTracks: [] });
    render(<DubTab {...makeProps()} resetDub={resetDub} />);

    fireEvent.click(screen.getByTestId('select-cookie'));
    expect(screen.getByTestId('cookie-name')).toHaveTextContent('cookies.txt');
    act(() => useAppStore.setState({ dubJobId: 'job1', dubStep: 'done' }));
    fireEvent.click(screen.getByTestId('reset-dub'));

    expect(resetDub).toHaveBeenCalledOnce();
    expect(screen.getByTestId('cookie-name')).toHaveTextContent('none');
  });

  it('retries an incomplete transcript directly but confirms before replacing a completed one', () => {
    const retry = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    useAppStore.setState({
      dubJobId: 'job1',
      dubStep: 'idle',
      dubSegments: [],
      dubTracks: [],
    });
    const { rerender } = render(<DubTab {...makeProps()} handleDubRetryTranscribe={retry} />);

    fireEvent.click(screen.getByRole('button', { name: 'transcribe' }));
    expect(retry).toHaveBeenCalledOnce();
    expect(confirm).not.toHaveBeenCalled();

    act(() =>
      useAppStore.setState({
        dubStep: 'idle',
        dubSegments: [{ id: 'partial', text: 'Incomplete transcript' }],
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'transcribe' }));
    expect(retry).toHaveBeenCalledTimes(2);
    expect(confirm).not.toHaveBeenCalled();

    act(() =>
      useAppStore.setState({
        dubStep: 'editing',
        dubSegments: [{ id: 's1', text: 'Complete transcript' }],
      }),
    );
    rerender(<DubTab {...makeProps()} handleDubRetryTranscribe={retry} />);

    fireEvent.click(screen.getByRole('button', { name: 'transcribe' }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(retry).toHaveBeenCalledTimes(2);

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'transcribe' }));
    expect(retry).toHaveBeenCalledTimes(3);
  });

  it('discards QC results when a newer generation finishes while verification is in flight', async () => {
    const loadingId = 'qc-loading';
    vi.spyOn(toast, 'loading').mockReturnValue(loadingId);
    const dismissSpy = vi.spyOn(toast, 'dismiss').mockImplementation(() => {});
    let resolveQc;
    vi.mocked(dubQc).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveQc = resolve;
        }),
    );
    useAppStore.setState({
      dubJobId: 'job1',
      dubStep: 'done',
      dubTracks: ['es'],
      dubLangCode: 'es',
      dubGenNonce: 1,
      dubSegments: [{ id: 's1', text: 'Old audio' }],
    });
    render(<DubTab {...makeProps()} />);

    let qcPromise;
    act(() => {
      qcPromise = captured.header.at(-1).handleDubQc();
    });
    expect(dubQc).toHaveBeenCalledWith('job1', 'es');

    act(() => {
      useAppStore.setState({
        dubGenNonce: 2,
        dubSegments: [{ id: 's1', text: 'New audio' }],
      });
    });
    await act(async () => {
      resolveQc({
        segments: [{ seg_id: 's1', drift: 0.8, flagged: true, recognized_text: 'Old' }],
        flagged_count: 1,
        total: 1,
      });
      await qcPromise;
    });

    expect(useAppStore.getState().dubSegments).toEqual([{ id: 's1', text: 'New audio' }]);
    expect(dismissSpy).toHaveBeenCalledWith(loadingId);
  });
});
