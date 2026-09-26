import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useAppStore } from '../store';
import '../i18n';

const captured = vi.hoisted(() => ({ header: [], left: [], right: [] }));
const pipeline = vi.hoisted(() => ({ tasks: new Map(), generated: [] }));
const dubApi = vi.hoisted(() => ({
  dubUpload: vi.fn(),
  dubIngestUrl: vi.fn(),
  dubAbort: vi.fn(),
  dubCleanupSegments: vi.fn(),
  dubTranslate: vi.fn(),
  dubGenerate: vi.fn(),
  tasksStreamUrl: vi.fn((id) => `/tasks/${id}`),
  tasksCancel: vi.fn(),
  transcribeStreamUrl: vi.fn(),
  dubImportSrt: vi.fn(),
  dubQc: vi.fn(),
  DUB_COOKIE_TRANSPORT_ERROR: 'transport',
  DUB_COOKIE_SIZE_ERROR: 'size',
}));
const clientApi = vi.hoisted(() => ({
  API: '',
  apiPost: vi.fn(),
  apiJson: vi.fn(() => new Promise(() => {})),
  apiFetch: vi.fn(),
}));

vi.mock('../api/dub', () => dubApi);
vi.mock('../api/client', () => clientApi);
vi.mock('../api/engines', () => ({
  listTranslationEngines: vi.fn(() => new Promise(() => {})),
  installTranslationEngine: vi.fn(),
}));
vi.mock('../hooks/useTimelineOnsets', () => ({ default: () => ({ onsets: [] }) }));
vi.mock('../utils/media', () => ({ playPing: vi.fn() }));
vi.mock('../utils/donationMoments', () => ({ recordValueMoment: vi.fn() }));
vi.mock('../utils/breadcrumbs', () => ({ addBreadcrumb: vi.fn() }));
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
vi.mock('../components/dub/DubRightColumn', () => ({
  default: (props) => {
    captured.right.push(props);
    return null;
  },
}));
vi.mock('../components/dub/DubFooter', () => ({ default: () => null }));
vi.mock('../components/dub/DubPipelineStepper', () => ({ default: () => null }));
vi.mock('../components/dub/IdleSkeleton', () => ({ default: () => null }));

import useDubWorkflow from '../hooks/useDubWorkflow';
import useSegmentEditing from '../hooks/useSegmentEditing';
import DubTab from '../pages/DubTab';
import ExportModal from '../components/ExportModal';

const noop = () => {};
const PICKS = [
  { lang: 'Hindi', code: 'hi' },
  { lang: 'Spanish', code: 'es' },
  { lang: 'French', code: 'fr' },
  { lang: 'German', code: 'de' },
];
const EXPECTED_CODES = ['bn', 'hi', 'es', 'fr', 'de'];
const baseState = useAppStore.getState();

function sseResponse(event) {
  const chunks = [new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)];
  return {
    body: {
      getReader: () => ({
        read: async () =>
          chunks.length ? { done: false, value: chunks.shift() } : { done: true, value: undefined },
      }),
    },
  };
}

function Harness() {
  const workflow = useDubWorkflow({
    loadProjects: vi.fn(),
    loadProfiles: vi.fn(),
    loadDubHistory: vi.fn(),
    setLastGenFingerprints: vi.fn(),
  });
  const editing = useSegmentEditing();
  return (
    <DubTab
      dubVideoFile={null}
      dubLocalBlobUrl={null}
      transcribeElapsed={0}
      translateProvider={workflow.translateProvider}
      setTranslateProvider={workflow.setTranslateProvider}
      showTranscript={false}
      setShowTranscript={noop}
      onGlossaryChange={noop}
      profiles={[]}
      segmentPreviewLoading={null}
      selectedSegIds={new Set()}
      setDubVideoFile={noop}
      setDubLocalBlobUrl={noop}
      handleDubAbort={workflow.handleDubAbort}
      handleDubUpload={workflow.handleDubUpload}
      handleDubIngestUrl={workflow.handleDubIngestUrl}
      handleDubRetryTranscribe={workflow.handleDubRetryTranscribe}
      handleDubStop={workflow.handleDubStop}
      handleDubGenerate={workflow.handleDubGenerate}
      handleDubImportSrt={workflow.handleDubImportSrt}
      handleDubDownload={noop}
      handleDubAudioDownload={noop}
      handleAudioExport={noop}
      handleSegmentPreview={noop}
      onDirectSegment={noop}
      handleTranslateAll={workflow.handleTranslateAll}
      handleCleanupSegments={workflow.handleCleanupSegments}
      incrementalPlan={null}
      triggerDownload={noop}
      fileToMediaUrl={noop}
      editSegments={useAppStore.getState().setDubSegments}
      saveProject={noop}
      resetDub={noop}
      segmentEditField={editing.segmentEditField}
      segmentDelete={noop}
      segmentRestoreOriginal={noop}
      segmentSplit={noop}
      segmentMerge={noop}
      segmentMoveResize={noop}
      timelineSelSegId={null}
      setTimelineSelSegId={noop}
      toggleSegSelect={noop}
      selectAllSegs={noop}
      clearSegSelection={noop}
      bulkApplyToSelected={noop}
      bulkDeleteSelected={noop}
    />
  );
}

beforeEach(() => {
  useAppStore.setState(baseState, true);
  captured.header.length = 0;
  captured.left.length = 0;
  captured.right.length = 0;
  pipeline.tasks.clear();
  pipeline.generated.length = 0;
  vi.clearAllMocks();
  useAppStore.setState({
    dubJobId: 'job1',
    dubStep: 'editing',
    dubLang: 'Bengali',
    dubLangCode: 'bn',
    multiLangMode: true,
    multiLangs: PICKS,
    dubTracks: [],
    dubSegments: [
      { id: '1', text: 'hello', text_original: 'hello', start: 0, end: 1 },
      { id: '2', text: 'world', text_original: 'world', start: 1, end: 2 },
    ],
  });
  dubApi.dubTranslate.mockImplementation(async (body) => ({
    translated: body.segments.map((segment) => ({
      id: segment.id,
      text: `${body.target_lang}:${segment.text}`,
    })),
    target_lang: body.target_lang,
  }));
  dubApi.dubGenerate.mockImplementation(async (_jobId, body) => {
    const taskId = `task-${body.language_code}`;
    pipeline.tasks.set(taskId, body);
    return { task_id: taskId };
  });
  clientApi.apiFetch.mockImplementation(async (url) => {
    const taskId = url.split('/').at(-1);
    const body = pipeline.tasks.get(taskId);
    pipeline.generated.push(body.language_code);
    return sseResponse({
      type: 'done',
      language_code: body.language_code,
      tracks: [...pipeline.generated],
      seg_hashes: { 1: `hash-${body.language_code}` },
    });
  });
});

describe('multi-language workflow integration', () => {
  it('shares one lock between translate and generate while a language batch is active', async () => {
    let releaseFirstTranslation;
    dubApi.dubTranslate.mockImplementationOnce(
      (body) =>
        new Promise((resolve) => {
          releaseFirstTranslation = () =>
            resolve({
              translated: body.segments.map((segment) => ({
                id: segment.id,
                text: `${body.target_lang}:${segment.text}`,
              })),
              target_lang: body.target_lang,
            });
        }),
    );
    render(<Harness />);

    let inFlight;
    await act(async () => {
      inFlight = captured.left.at(-1).handleTranslateAll();
      await Promise.resolve();
    });

    expect(captured.right.at(-1).multiBatchBusy).toBe(true);
    await act(async () => {
      await captured.header.at(-1).onGenerateClick();
      await captured.left.at(-1).handleTranslateAll();
    });
    expect(dubApi.dubTranslate).toHaveBeenCalledTimes(1);
    expect(dubApi.dubGenerate).not.toHaveBeenCalled();

    releaseFirstTranslation();
    await act(async () => {
      await inFlight;
    });
    expect(captured.right.at(-1).multiBatchBusy).toBe(false);
  });

  it('translates primary + 4 chips, generates their own texts/tracks, and exposes all to export', async () => {
    render(<Harness />);

    act(() => {
      captured.right.at(-1).setDubLang('Spanish');
      captured.right.at(-1).setDubLangCode('es');
    });
    await act(async () => {
      await captured.left.at(-1).handleTranslateAll();
    });

    expect(dubApi.dubTranslate.mock.calls.map(([body]) => body.target_lang)).toEqual(
      EXPECTED_CODES,
    );
    let state = useAppStore.getState();
    expect(state.dubLangCode).toBe('bn');
    for (const code of EXPECTED_CODES) {
      expect(state.dubSegments.map((segment) => segment.translations[code])).toEqual([
        `${code}:hello`,
        `${code}:world`,
      ]);
      expect(captured.left.at(-1).multiLangProgress[code]).toEqual({ ready: 2, total: 2 });
    }

    act(() => {
      captured.right.at(-1).setDubLang('Spanish');
      captured.right.at(-1).setDubLangCode('es');
      captured.right.at(-1).segmentEditField('1', 'text', 'es:edited');
      captured.right.at(-1).setDubLang('Hindi');
      captured.right.at(-1).setDubLangCode('hi');
    });
    expect(useAppStore.getState().dubSegments[0].text).toBe('hi:hello');
    act(() => {
      captured.right.at(-1).setDubLang('Spanish');
      captured.right.at(-1).setDubLangCode('es');
    });
    expect(useAppStore.getState().dubSegments[0].text).toBe('es:edited');
    expect(useAppStore.getState().dubSegments[0].translations.hi).toBe('hi:hello');

    await act(async () => {
      await captured.header.at(-1).onGenerateClick();
    });

    // Complete cached translations are reused; generation receives the text
    // belonging to each code rather than the currently visible language.
    expect(dubApi.dubTranslate).toHaveBeenCalledTimes(5);
    expect(dubApi.dubGenerate.mock.calls.map(([, body]) => body.language_code)).toEqual(
      EXPECTED_CODES,
    );
    for (const [, body] of dubApi.dubGenerate.mock.calls) {
      const firstText = body.language_code === 'es' ? 'es:edited' : `${body.language_code}:hello`;
      expect(body.segments.map((segment) => segment.text)).toEqual([
        firstText,
        `${body.language_code}:world`,
      ]);
    }
    state = useAppStore.getState();
    expect(state.dubTracks).toEqual(EXPECTED_CODES);
    expect(state.dubLangCode).toBe('bn');

    const exported = [];
    render(
      <ExportModal
        open
        onClose={noop}
        jobId="job1"
        filename="video.mp4"
        dubTracks={state.dubTracks}
        dubLangCode={state.dubLangCode}
        preserveBg={false}
        setPreserveBg={noop}
        defaultTrack="bn"
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
        segmentCount={2}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /tracks 6\/6/i }));
    for (const code of EXPECTED_CODES)
      expect(screen.getByLabelText(new RegExp(`^${code.toUpperCase()}`))).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Export MP4' }));
    expect(exported).toEqual(EXPECTED_CODES);
  });
});
