import { translationActivity } from './translation-activity';
import { ingestDubUrl, isDubUrl } from './dub-session';
import { expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api/client';
import { consumeTaskStream, IncompleteTaskStreamError } from '@/lib/api/event-stream';
import {
  applyDubTranslationRows,
  uploadDub,
  setDubProduction,
  dubSession,
  generateDub,
  resumeDub,
  editDubSegment,
  clearDubEditHistory,
  clearDubDraftForReset,
  DUB_AGENT_UNAVAILABLE,
  deleteDubSegment,
  importDubSubtitles,
  insertDubSegment,
  mergeDubSegment,
  moveResizeDubSegment,
  cancelDub,
  dubCancelling,
  redoDubEdit,
  resetDubSession,
  translateDub,
  translateDubBatchWithAgent,
  translateDubWithAgent,
  setDubTarget,
  splitDubSegment,
  undoDubEdit,
} from './dub-session';
vi.mock('@/lib/api/client', () => ({ apiJson: vi.fn() }));
vi.mock('@/lib/api/event-stream', async (load) => ({
  ...(await load<typeof import('@/lib/api/event-stream')>()),
  consumeTaskStream: vi.fn(),
}));

it('translates the current dubbing segments with an installed local CLI agent', async () => {
  let logListener: ((event: { requestId: string; text: string }) => void) | undefined;
  const unsubscribe = vi.fn();

  const translate = vi.fn().mockImplementation(async (request) => {
    logListener?.({ requestId: 'another-request', text: 'must not appear' });
    logListener?.({ requestId: request.requestId, text: 'Translating two segments' });
    return {
    agent: 'codex',
    translations: [
      { id: 'a', text: 'Hola' },
      { id: 'b', text: 'Adiós' },
    ],
  }; });
  Object.defineProperty(window, 'voicestudio', {
    configurable: true,
    value: {
      repair: { translate, stopTranslation: vi.fn().mockResolvedValue(undefined),
        onTranslationEvent: (callback: typeof logListener) => { logListener = callback; return unsubscribe; },
      },
    } as unknown as Window['voicestudio'],
  });
  vi.mocked(apiJson).mockReset();
  vi.mocked(apiJson).mockResolvedValueOnce([
    { source: 'VoiceStudio', target: 'VoiceStudio', note: '' },
  ]);
  dubSession.setState((current) => ({
    ...current,
    jobId: 'agent-job',
    phase: 'editing',
    recovery: null,
    sourceLang: 'en',
    dialect: 'es-MX',
    translationInstructions: 'Warm and conversational',
    segments: [
      { id: 'a', start: 0, end: 1.25, text: 'Hello', text_original: 'Hello' },
      { id: 'b', start: 1.25, end: 3, text: 'Goodbye', text_original: 'Goodbye' },
    ],
  }));

  await expect(translateDubWithAgent('es', 'codex')).resolves.toBe(true);
  expect(unsubscribe).toHaveBeenCalledOnce();
  expect(translationActivity.state.runs.at(-1)).toMatchObject({
    status: 'complete', logs: 'Translating two segments',
  });

  expect(translate).toHaveBeenCalledWith(
    expect.objectContaining({
      agent: 'codex',
      purpose: 'translate',
      sourceLanguage: 'en',
      targetLanguage: 'es',
      dialect: 'es-MX',
      translationInstructions: 'Warm and conversational',
      segments: [
        expect.objectContaining({ id: 'a', sourceText: 'Hello', start: 0, end: 1.25 }),
        expect.objectContaining({ id: 'b', sourceText: 'Goodbye', start: 1.25, end: 3 }),
      ],
    }),
  );
  expect(dubSession.state).toMatchObject({ quality: 'agent', agentCli: 'codex', phase: 'editing' });
  expect(dubSession.state.segments.map((segment) => segment.text)).toEqual(['Hola', 'Adiós']);
  expect(dubSession.state.segments.map((segment) => segment.translations?.es)).toEqual([
    'Hola',
    'Adiós',
  ]);
  expect(dubSession.state.segments.map((segment) => segment.agent_generated_langs)).toEqual([
    ['es'],
    ['es'],
  ]);
  dubSession.setState((current) => ({ ...current, quality: 'fast', agentCli: undefined }));
  vi.clearAllMocks();
  delete (window as Partial<Window>).voicestudio;
});

it('keeps timing-fit provenance for every agent-translated target language', async () => {
  const translate = vi.fn(
    async (request: { targetLanguage: string; segments: Array<{ id: string }> }) => ({
      agent: 'codex',
      translations: request.segments.map((segment) => ({
        id: segment.id,
        text: `${request.targetLanguage} line`,
      })),
    }),
  );
  Object.defineProperty(window, 'voicestudio', {
    configurable: true,
    value: {
      repair: { translate, stopTranslation: vi.fn().mockResolvedValue(undefined) },
    } as unknown as Window['voicestudio'],
  });
  vi.mocked(apiJson).mockReset().mockResolvedValue([]);
  dubSession.setState((current) => ({
    ...current,
    jobId: 'agent-batch',
    phase: 'editing',
    recovery: null,
    sourceLang: 'en',
    quality: 'fast',
    agentCli: undefined,
    segments: [{ id: 'a', start: 0, end: 2, text: 'Hello', text_original: 'Hello' }],
  }));

  await expect(
    translateDubBatchWithAgent(
      [
        { lang: 'Spanish', code: 'es' },
        { lang: 'French', code: 'fr' },
      ],
      'codex',
    ),
  ).resolves.toEqual([]);

  expect(translate).toHaveBeenCalledTimes(2);
  expect(dubSession.state.segments[0]).toMatchObject({
    text: 'Spanish line',
    translations: { es: 'Spanish line', fr: 'French line' },
    agent_generated_lang: 'es',
    agent_generated_langs: ['es', 'fr'],
  });
  setDubTarget('French', 'fr');
  expect(dubSession.state.segments[0]).toMatchObject({
    text: 'French line',
    agent_generated_lang: 'fr',
  });
  editDubSegment('a', { text: 'Manually edited' });
  expect(dubSession.state.segments[0]).toMatchObject({
    agent_generated_langs: ['es'],
  });
  expect(dubSession.state.segments[0].agent_generated_lang).toBeUndefined();
  dubSession.setState((current) => ({ ...current, quality: 'fast', agentCli: undefined }));
  vi.clearAllMocks();
  delete (window as Partial<Window>).voicestudio;
});

it('supports reversible insert, delete, merge, and split edits without losing speaker attribution', () => {
  const original = [
    {
      id: 'a',
      start: 0,
      end: 1,
      text: 'Hello',
      text_original: 'Hello',
      speaker_id: 'SPEAKER_00',
      profile_id: 'voice-a',
      translations: { es: 'Hola' },
    },
    {
      id: 'b',
      start: 2,
      end: 3,
      text: 'world',
      text_original: 'world',
      speaker_id: 'SPEAKER_01',
      profile_id: 'voice-b',
      translations: { es: 'mundo' },
    },
  ];
  dubSession.setState((current) => ({
    ...current,
    phase: 'editing',
    recovery: null,
    segments: original,
  }));
  clearDubEditHistory();

  insertDubSegment('a');
  expect(dubSession.state.segments).toHaveLength(3);
  expect(dubSession.state.segments[1]).toMatchObject({
    id: 'a_new',
    start: 1,
    end: 2,
    text: '',
    speaker_id: 'SPEAKER_00',
    profile_id: 'voice-a',
  });
  undoDubEdit();
  expect(dubSession.state.segments).toEqual(original);
  redoDubEdit();
  expect(dubSession.state.segments).toHaveLength(3);
  deleteDubSegment('a_new');
  expect(dubSession.state.segments).toEqual(original);

  mergeDubSegment('a', 'next');
  expect(dubSession.state.segments).toHaveLength(1);
  expect(dubSession.state.segments[0]).toMatchObject({
    id: 'a',
    text: 'Hello world',
    text_original: 'Hello world',
    start: 0,
    end: 3,
    translations: { es: 'Hola mundo' },
  });
  splitDubSegment('a', 6);
  expect(dubSession.state.segments).toMatchObject([
    { id: 'a_a', text: 'Hello', speaker_id: 'SPEAKER_00', profile_id: 'voice-a' },
    { id: 'a_b', text: 'world', speaker_id: 'SPEAKER_01', profile_id: 'voice-b' },
  ]);
  undoDubEdit();
  expect(dubSession.state.segments).toHaveLength(1);
  moveResizeDubSegment('a', { start: 0, end: 1.5 });
  expect(dubSession.state.segments[0]).toMatchObject({ end: 1.5, speed: 2 });
  undoDubEdit();
  expect(dubSession.state.segments[0]).toMatchObject({ end: 3 });
  editDubSegment('a', { profile_id: 'voice-new' });
  splitDubSegment('a', 6);
  expect(dubSession.state.segments.map((segment) => segment.profile_id)).toEqual([
    'voice-new',
    'voice-new',
  ]);
});

it('coalesces a burst of transcript typing into one undo step', () => {
  dubSession.setState((current) => ({
    ...current,
    phase: 'editing',
    recovery: null,
    segments: [{ id: 'typed', start: 0, end: 1, text: '', text_original: '' }],
  }));
  clearDubEditHistory();

  editDubSegment('typed', { text: 'H' }, { historyGroup: 'text:typed' });
  editDubSegment('typed', { text: 'He' }, { historyGroup: 'text:typed' });
  editDubSegment('typed', { text: 'Hello' }, { historyGroup: 'text:typed' });
  expect(dubSession.state.segments[0].text).toBe('Hello');

  undoDubEdit();
  expect(dubSession.state.segments[0].text).toBe('');
  redoDubEdit();
  expect(dubSession.state.segments[0].text).toBe('Hello');
});

it('waits for preparation before transcription and preserves speaker bindings for generation', async () => {
  vi.mocked(apiJson).mockResolvedValueOnce({ job_id: 'job', task_id: 'prep' });
  vi.mocked(consumeTaskStream)
    .mockImplementationOnce(async (_path, emit) => {
      emit({ type: 'ready', duration: 6 });
    })
    .mockImplementationOnce(async (_path, emit) => {
      emit({
        type: 'final',
        source_lang: 'en',
        segments: [{ id: 1, start: 0, end: 2, text: 'Hello', speaker_id: 'SPEAKER_00' }],
        cast_sources: { SPEAKER_00: {} },
      });
      emit({ type: 'done' });
    });
  await uploadDub(new File(['audio'], 'voice.wav', { type: 'audio/wav' }));
  expect(dubSession.state.phase).toBe('editing');
  expect(dubSession.state.duration).toBe(6);
  expect(dubSession.state.segments[0]).toMatchObject({
    id: '1',
    text_original: 'Hello',
    profile_id: 'auto:speaker00',
  });
  expect(vi.mocked(consumeTaskStream).mock.calls.map((call) => call[0])).toEqual([
    '/tasks/stream/prep',
    '/dub/transcribe-stream/job',
  ]);
  vi.mocked(apiJson).mockResolvedValueOnce({ task_id: 'generate' });
  vi.mocked(consumeTaskStream).mockImplementationOnce(async (_path, emit) => {
    emit({ type: 'done', tracks: ['es'] });
  });
  await generateDub('Spanish', 'es');
  expect(dubSession.state.phase).toBe('done');
  expect(
    JSON.parse(vi.mocked(apiJson).mock.calls[1][1]!.body as string).segments[0].profile_id,
  ).toBe('auto:speaker00');
});
it('a dropped preparation stream never advances to transcription or done', async () => {
  vi.mocked(consumeTaskStream).mockClear().mockRejectedValueOnce(new IncompleteTaskStreamError());
  vi.mocked(apiJson).mockResolvedValueOnce({ job_id: 'job2', task_id: 'prep2' });
  await uploadDub(new File(['audio'], 'voice.wav', { type: 'audio/wav' }));
  expect(dubSession.state.phase).toBe('idle');
  expect(dubSession.state.error).toContain('without a terminal event');
  expect(consumeTaskStream).toHaveBeenCalledOnce();
  expect(dubSession.state.recovery).toBe('preparing');
  vi.mocked(apiJson).mockClear();
  await uploadDub(new File(['other'], 'other.wav', { type: 'audio/wav' }));
  expect(apiJson).not.toHaveBeenCalled();
});

it('resumes the existing generation task instead of generating duplicate audio', async () => {
  vi.mocked(apiJson).mockClear().mockResolvedValueOnce({ status: 'done' });
  vi.mocked(consumeTaskStream)
    .mockClear()
    .mockImplementationOnce(async (_path, emit) => {
      emit({ type: 'done', tracks: ['fr'] });
    });
  dubSession.setState((current) => ({
    ...current,
    jobId: 'original-job',
    taskId: 'existing-task',
    phase: 'editing',
    recovery: 'generating',
  }));
  await resumeDub();
  expect(apiJson).toHaveBeenCalledExactlyOnceWith(
    '/jobs/existing-task',
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(consumeTaskStream).toHaveBeenCalledWith(
    '/tasks/stream/existing-task',
    expect.any(Function),
    expect.any(AbortSignal),
  );
  expect(dubSession.state).toMatchObject({ phase: 'done', tracks: ['fr'], recovery: null });
});

it('preserves a disconnected generation and blocks edits and duplicate generation', async () => {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'job',
    recovery: null,
    segments: [{ id: '1', start: 0, end: 2, text: 'Hello', text_original: 'Hello' }],
  }));
  vi.mocked(apiJson).mockClear().mockResolvedValueOnce({ task_id: 'running' });
  vi.mocked(consumeTaskStream).mockRejectedValueOnce(new IncompleteTaskStreamError());
  await generateDub('Spanish', 'es');
  expect(dubSession.state).toMatchObject({ recovery: 'generating', taskId: 'running' });
  editDubSegment('1', { text: 'Changed' });
  await generateDub('French', 'fr');
  expect(dubSession.state.segments[0].text).toBe('Hello');
  expect(apiJson).toHaveBeenCalledTimes(1);
});
it('a terminal backend failure releases recovery instead of trapping the draft', async () => {
  vi.mocked(apiJson).mockResolvedValueOnce({ status: 'error' });
  vi.mocked(consumeTaskStream).mockImplementationOnce(async (_path, emit) => {
    emit({ type: 'error', reason: 'fixture failure' });
  });
  await resumeDub();
  expect(dubSession.state).toMatchObject({
    recovery: null,
    taskId: null,
    phase: 'editing',
    error: 'fixture failure',
  });
});

it('imports SRT using server-matched voice references and invalidates stale tracks', async () => {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'job',
    recovery: null,
    tracks: ['es'],
    segments: [{ id: '1', start: 0, end: 2, text: 'Old', text_original: 'Old' }],
  }));
  vi.mocked(apiJson)
    .mockClear()
    .mockResolvedValueOnce({
      segments: [{ id: 0, start: 1, end: 3, text: 'Subtitle', profile_id: 'matched-reference' }],
      stats: { imported: 1, skipped_malformed: 2 },
    });
  await importDubSubtitles(new File(['subtitle'], 'captions.srt'));
  expect(apiJson).toHaveBeenCalledWith(
    '/dub/import-srt/job',
    expect.objectContaining({ method: 'POST', body: expect.any(FormData) }),
  );
  expect(dubSession.state).toMatchObject({
    phase: 'editing',
    tracks: [],
    segments: [
      { id: '0', start: 1, end: 3, text_original: 'Subtitle', profile_id: 'matched-reference' },
    ],
    subtitleImport: { file: 'captions.srt', stats: { imported: 1, skipped_malformed: 2 } },
  });
});
it('keeps edited segments when subtitle import fails and blocks import during recovery', async () => {
  const segments = dubSession.state.segments;
  vi.mocked(apiJson).mockClear().mockRejectedValueOnce(new Error('Invalid subtitle'));
  await importDubSubtitles(new File(['bad'], 'bad.srt'));
  expect(dubSession.state.segments).toBe(segments);
  expect(dubSession.state.error).toBe('Invalid subtitle');
  dubSession.setState((current) => ({ ...current, recovery: 'generating' }));
  await importDubSubtitles(new File(['other'], 'other.srt'));
  expect(apiJson).toHaveBeenCalledTimes(1);
});

it('validates URL schemes without network calls and waits for preparation before ASR', async () => {
  dubSession.setState((current) => ({ ...current, recovery: null }));
  vi.mocked(apiJson).mockClear();
  expect(isDubUrl('file:///private/movie.mp4')).toBe(false);
  expect(isDubUrl('javascript:alert(1)')).toBe(false);
  await ingestDubUrl('invalid');
  expect(apiJson).not.toHaveBeenCalled();
  vi.mocked(apiJson).mockResolvedValueOnce({ job_id: 'remote', task_id: 'prep-remote' });
  vi.mocked(consumeTaskStream)
    .mockClear()
    .mockImplementationOnce(async (_path, emit) => {
      emit({ type: 'ready' });
    })
    .mockImplementationOnce(async (_path, emit) => {
      emit({ type: 'final', segments: [{ id: 0, start: 0, end: 1, text: 'Remote' }] });
      emit({ type: 'done' });
    });
  await ingestDubUrl(' https://example.com/movie.mp4 ');
  expect(JSON.parse(vi.mocked(apiJson).mock.calls[0][1]!.body as string)).toMatchObject({
    url: 'https://example.com/movie.mp4',
  });
  expect(vi.mocked(consumeTaskStream).mock.calls.map((call) => call[0])).toEqual([
    '/tasks/stream/prep-remote',
    '/dub/transcribe-stream/remote',
  ]);
  expect(dubSession.state).toMatchObject({
    phase: 'editing',
    inputType: 'video',
    segments: [{ text: 'Remote' }],
  });
});

it('retries interrupted ASR against prepared media without uploading or preparing again', async () => {
  dubSession.setState((current) => ({ ...current, recovery: null, phase: 'idle' }));
  vi.mocked(apiJson).mockClear().mockResolvedValueOnce({ job_id: 'prepared', task_id: 'prep' });
  vi.mocked(consumeTaskStream)
    .mockClear()
    .mockImplementationOnce(async (_path, emit) => {
      emit({ type: 'ready' });
    })
    .mockRejectedValueOnce(new IncompleteTaskStreamError());
  await uploadDub(new File(['audio'], 'voice.wav', { type: 'audio/wav' }));
  expect(dubSession.state).toMatchObject({
    jobId: 'prepared',
    phase: 'idle',
    recovery: 'transcribing',
    taskId: null,
  });
  await uploadDub(new File(['other'], 'other.wav'));
  expect(apiJson).toHaveBeenCalledTimes(1);
  vi.mocked(apiJson).mockClear();
  vi.mocked(consumeTaskStream)
    .mockClear()
    .mockImplementationOnce(async (_path, emit) => {
      emit({
        type: 'final',
        segments: [{ id: '1', start: 0, end: 1, text: 'Recovered' }],
        source_lang: 'en',
      });
      emit({ type: 'done' });
    });
  await resumeDub();
  expect(apiJson).not.toHaveBeenCalled();
  expect(consumeTaskStream).toHaveBeenCalledExactlyOnceWith(
    '/dub/transcribe-stream/prepared',
    expect.any(Function),
    expect.any(AbortSignal),
  );
  expect(dubSession.state).toMatchObject({
    recovery: null,
    phase: 'editing',
    segments: [{ text: 'Recovered' }],
  });
});

it('passes explicit source language and speaker hints to file and URL analysis', async () => {
  dubSession.setState((current) => ({ ...current, phase: 'idle', recovery: null }));
  setDubProduction({ sourceLanguage: 'fr', numSpeakers: 2 });
  for (const upload of [true, false]) {
    vi.mocked(apiJson).mockClear().mockResolvedValueOnce({ job_id: 'hints', task_id: 'prep' });
    vi.mocked(consumeTaskStream)
      .mockClear()
      .mockImplementation(async (path, emit) => {
        if (path.startsWith('/tasks')) emit({ type: 'ready' });
        else {
          emit({ type: 'final', segments: [], source_lang: 'fr' });
          emit({ type: 'done' });
        }
      });
    if (upload) await uploadDub(new File(['audio'], 'french.wav', { type: 'audio/wav' }));
    else await ingestDubUrl('https://example.com/french.mp4');
    const body = vi.mocked(apiJson).mock.calls[0][1]!.body;
    expect(
      upload ? (body as FormData).get('source_lang') : JSON.parse(body as string).source_lang,
    ).toBe('fr');
    expect(consumeTaskStream).toHaveBeenLastCalledWith(
      '/dub/transcribe-stream/hints?num_speakers=2',
      expect.any(Function),
      expect.any(AbortSignal),
    );
  }
});

it('uses saved Smart Fit overrides only for Smart Fit generation', async () => {
  for (const strategy of ['smart_fit', 'strict_slot'] as const) {
    dubSession.setState((current) => ({
      ...current,
      jobId: 'fit',
      phase: 'editing',
      recovery: null,
      timingStrategy: strategy,
      fitOptions: { allow_video_retime: false, audio_rate_cap: 1.3 },
      segments: [
        {
          id: '1',
          start: 0,
          end: 1,
          text: 'Test',
          text_original: 'Test',
          gain: 0,
          speed: 1.2,
          direction: 'whispered',
        },
      ],
    }));
    vi.mocked(apiJson).mockClear().mockResolvedValueOnce({ task_id: 'fit-task' });
    vi.mocked(consumeTaskStream).mockImplementationOnce(async (_path, emit) => {
      emit({ type: 'done', tracks: ['en'] });
    });
    await generateDub('English', 'en');
    const request = JSON.parse(vi.mocked(apiJson).mock.calls[0][1]!.body as string);
    expect(request.segments[0]).toMatchObject({ gain: 0, speed: 1.2, direction: 'whispered' });
    expect(request.fit_options).toEqual(
      strategy === 'smart_fit' ? { allow_video_retime: false, audio_rate_cap: 1.3 } : undefined,
    );
  }
});

it('does not recreate a cleared draft from a pending write or pagehide', async () => {
  localStorage.setItem('voicestudio.dub.session.v1', 'old');
  dubSession.setState((current) => ({ ...current, speed: 1.1 }));
  clearDubDraftForReset();
  window.dispatchEvent(new Event('pagehide'));
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(localStorage.getItem('voicestudio.dub.session.v1')).toBeNull();
});

it('does not generate-ready an Agent Fit translation that fell back without an LLM', async () => {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'agent-fit',
    phase: 'editing',
    recovery: null,
    quality: 'agent',
    translationFallback: false,
    segments: [{ id: '1', start: 0, end: 2, text: 'Hello', text_original: 'Hello' }],
  }));
  vi.mocked(apiJson)
    .mockClear()
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce({
      cinematic_skipped: 'no-llm-configured',
      translated: [{ id: '1', text: 'Hola' }],
    });

  await expect(translateDub('es', 'google')).resolves.toBe(false);
  expect(dubSession.state).toMatchObject({
    phase: 'editing',
    quality: 'agent',
    translationFallback: true,
    segments: [{ text: 'Hola', agent_generated_lang: undefined }],
  });
});

it('stops Agent Fit when the slot-fitting provider disappears mid-run', async () => {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'agent-fit-provider-loss',
    phase: 'editing',
    recovery: null,
    quality: 'agent',
    segments: [
      {
        id: '1',
        start: 0,
        end: 2,
        text: 'Una traducción larga',
        text_original: 'A long translation',
        agent_generated_lang: 'es',
      },
    ],
  }));
  vi.mocked(apiJson)
    .mockClear()
    .mockResolvedValueOnce({ task_id: 'agent-render' })
    .mockResolvedValueOnce({
      segments: [
        {
          id: '1',
          text: 'Una traducción larga',
          changed: false,
          error: 'fit-provider-failed',
        },
      ],
    });
  vi.mocked(consumeTaskStream)
    .mockClear()
    .mockImplementationOnce(async (_path, emit) => {
      emit({ type: 'done', tracks: ['es'], sync_scores: [1.25] });
    });

  await expect(generateDub('Spanish', 'es')).resolves.toBe(false);
  expect(apiJson).toHaveBeenCalledTimes(2);
  expect(dubSession.state).toMatchObject({
    phase: 'editing',
    error: DUB_AGENT_UNAVAILABLE,
    tracks: ['es'],
  });
});

it('echoes an imported cue only while no paste or edit has replaced its words (#2295)', async () => {
  const source = { id: 'imp:0', text: 'Hello', cue: '<i>Hello</i>' };
  const reset = () =>
    dubSession.setState((current) => ({
      ...current,
      jobId: 'cue',
      phase: 'editing',
      recovery: null,
      timingStrategy: 'strict_slot',
      segments: [
        {
          id: '0',
          start: 0,
          end: 1,
          text: 'Hello',
          text_original: 'Hello',
          srt_source: source,
          cue_source_id: 'imp:0',
        },
      ],
    }));
  const generatedCueId = async () => {
    vi.mocked(apiJson).mockClear().mockResolvedValueOnce({ task_id: 'cue-task' });
    vi.mocked(consumeTaskStream).mockImplementationOnce(async (_path, emit) => {
      emit({ type: 'done', tracks: ['en'] });
    });
    await generateDub('English', 'en');
    const body = JSON.parse(vi.mocked(apiJson).mock.calls[0][1]!.body as string);
    return body.segments[0].cue_source_id;
  };

  reset();
  expect(await generatedCueId()).toBe('imp:0');

  // A pasted translation that reads exactly like the import is still not it.
  reset();
  applyDubTranslationRows('en', [
    { id: '0', index: 0, start: 0, end: 1, before: 'Hello', after: 'Hello', matched: true },
  ]);
  expect(dubSession.state.segments[0].cue_source_id).toBeUndefined();
  expect(await generatedCueId()).toBeUndefined();

  // Editing away and back never revives it.
  reset();
  editDubSegment('0', { text: 'Bye' });
  editDubSegment('0', { text: 'Hello' });
  expect(await generatedCueId()).toBeUndefined();

  // Switching to a translation and back to the untouched original restores it.
  reset();
  dubSession.setState((current) => ({
    ...current,
    segments: current.segments.map((segment) => ({ ...segment, translations: { es: 'Hola' } })),
  }));
  setDubTarget('Spanish', 'es');
  expect(dubSession.state.segments[0].cue_source_id).toBeUndefined();
  setDubTarget('English', 'en');
  expect(dubSession.state.segments[0].text).toBe('Hello');
  expect(await generatedCueId()).toBe('imp:0');
});

it('resetDubSession drops the loaded source but keeps production preferences', () => {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'remove-me',
    filename: 'clip.mp4',
    phase: 'done',
    segments: [
      {
        id: '0',
        start: 0,
        end: 1,
        text: 'Hello',
        text_original: 'Hello',
        translations: {},
      },
    ],
    error: 'boom',
    target: 'French',
    quality: 'cinematic',
  }));
  resetDubSession();
  expect(dubSession.state.jobId).toBeNull();
  expect(dubSession.state.filename).toBe('');
  expect(dubSession.state.segments).toEqual([]);
  expect(dubSession.state.phase).toBe('idle');
  expect(dubSession.state.error).toBeNull();
  expect(dubSession.state.target).toBe('French');
  expect(dubSession.state.quality).toBe('cinematic');
});

it('cancelDub exposes cancel-in-flight state so removal stays disabled', async () => {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'cancel-job',
    taskId: null,
    recovery: 'transcribing',
    phase: 'transcribing',
  }));
  let resolveAbort!: (value: unknown) => void;
  vi.mocked(apiJson).mockReset();
  vi.mocked(apiJson).mockImplementationOnce(
    () => new Promise((resolve) => { resolveAbort = resolve; }),
  );
  const pending = cancelDub();
  expect(dubCancelling.state).toBe(true);
  resolveAbort({});
  await pending;
  expect(dubCancelling.state).toBe(false);
  dubSession.setState((current) => ({
    ...current,
    jobId: null,
    recovery: null,
    phase: 'idle',
  }));
});
