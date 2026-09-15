import { expect, it } from 'vitest';
import { restoreDubDraft } from './dub-draft';
import type { DubSession } from './dub-session';
const defaults: DubSession = {
  jobId: null,
  taskId: null,
  filename: '',
  inputType: 'video',
  phase: 'idle',
  segments: [],
  sourceLang: '',
  tracks: [],
  event: null,
  error: null,
  recovery: null,
  target: 'Spanish',
  quality: 'fast',
};
it('restores edits but requires explicit reconnection for interrupted generation', () => {
  const result = restoreDubDraft(
    JSON.stringify({
      ...defaults,
      jobId: 'job',
      taskId: 'task',
      phase: 'generating',
      target: 'French',
      segments: [{ id: '1', start: 0, end: 2, text: 'Edited' }],
    }),
    defaults,
  );
  expect(result).toMatchObject({
    phase: 'editing',
    recovery: 'generating',
    taskId: 'task',
    target: 'French',
  });
  expect(result.segments[0]).toMatchObject({ text: 'Edited', text_original: 'Edited' });
});
it('rejects malformed drafts, unsafe IDs and invalid timings', () => {
  expect(restoreDubDraft('{', defaults)).toEqual(defaults);
  const result = restoreDubDraft(
    JSON.stringify({
      ...defaults,
      jobId: '../bad',
      taskId: 'bad/path',
      phase: 'generating',
      segments: [null, { id: '1', start: 4, end: 2, text: 'Bad' }],
    }),
    defaults,
  );
  expect(result).toMatchObject({
    jobId: null,
    taskId: null,
    phase: 'idle',
    recovery: null,
    segments: [],
  });
});

it('preserves translation quality and rejects unknown stored modes', () => {
  expect(
    restoreDubDraft(JSON.stringify({ ...defaults, quality: 'cinematic' }), defaults).quality,
  ).toBe('cinematic');
  expect(
    restoreDubDraft(JSON.stringify({ ...defaults, quality: 'invalid' }), defaults).quality,
  ).toBe('fast');
});

it('restores a local agent and timing-fit provenance for every translated language', () => {
  const restored = restoreDubDraft(
    JSON.stringify({
      ...defaults,
      quality: 'agent',
      agentCli: 'codex',
      segments: [
        {
          id: '1',
          start: 0,
          end: 2,
          text: 'Hola',
          text_original: 'Hello',
          translations: { es: 'Hola', fr: 'Bonjour' },
          agent_generated_lang: 'es',
          agent_generated_langs: ['es', 'fr', 'bad/path', 'fr'],
        },
      ],
    }),
    defaults,
  );
  expect(restored).toMatchObject({ quality: 'agent', agentCli: 'codex' });
  expect(restored.segments[0]).toMatchObject({
    agent_generated_lang: 'es',
    agent_generated_langs: ['es', 'fr'],
  });
});

it('restores generation choices and timing needed by resumed exports', () => {
  const restored = restoreDubDraft(
    JSON.stringify({
      ...defaults,
      timingStrategy: 'smart_fit',
      voiceMatch: 'consistent',
      pendingTiming: 'stretch_video',
      generatedTiming: 'strict_slot',
    }),
    defaults,
  );
  expect(restored).toMatchObject({
    timingStrategy: 'smart_fit',
    voiceMatch: 'consistent',
    pendingTiming: 'stretch_video',
    generatedTiming: 'strict_slot',
  });
  expect(
    restoreDubDraft(
      JSON.stringify({ ...defaults, timingStrategy: 'bad', voiceMatch: 'bad' }),
      defaults,
    ),
  ).toMatchObject({ timingStrategy: 'strict_slot', voiceMatch: 'per_line' });
});

it('offers explicit transcription retry after reload without a task ID', () => {
  expect(
    restoreDubDraft(
      JSON.stringify({ ...defaults, jobId: 'prepared', phase: 'transcribing' }),
      defaults,
    ),
  ).toMatchObject({ jobId: 'prepared', phase: 'idle', taskId: null, recovery: 'transcribing' });
});

it('preserves valid analysis hints and drops invalid saved values', () => {
  expect(
    restoreDubDraft(
      JSON.stringify({ ...defaults, sourceLanguage: 'fr', numSpeakers: 2 }),
      defaults,
    ),
  ).toMatchObject({ sourceLanguage: 'fr', numSpeakers: 2 });
  expect(
    restoreDubDraft(
      JSON.stringify({ ...defaults, sourceLanguage: 'invalid/path', numSpeakers: 99 }),
      defaults,
    ),
  ).toMatchObject({ sourceLanguage: undefined, numSpeakers: undefined });
});

it('preserves zero guidance and rejects out-of-range stored production settings', () => {
  expect(
    restoreDubDraft(
      JSON.stringify({ ...defaults, steps: 64, guidance: 0, speed: 0.5, instruct: 'calm' }),
      defaults,
    ),
  ).toMatchObject({ steps: 64, guidance: 0, speed: 0.5, instruct: 'calm' });
  expect(
    restoreDubDraft(JSON.stringify({ ...defaults, steps: -1, guidance: 99, speed: 0 }), defaults),
  ).toMatchObject({ steps: undefined, guidance: undefined, speed: undefined });
});

it('recovers provider error pages saved as translated dialogue', () => {
  const errorPage =
    "Error 500 (Server Error)!!500. That's an error. There was an error. Please try again later.";
  const restored = restoreDubDraft(
    JSON.stringify({
      ...defaults,
      segments: [
        {
          id: '1',
          start: 0,
          end: 2,
          text: errorPage,
          text_original: 'Keep this dialogue',
          translations: { es: errorPage },
        },
      ],
    }),
    defaults,
  );
  expect(restored.segments[0]).toMatchObject({
    text: 'Keep this dialogue',
    text_original: 'Keep this dialogue',
    translate_error: 'translation provider returned invalid output',
  });
  expect(restored.segments[0].translations).toBeUndefined();
});

it('restores the project translation brief without changing its wording', () => {
  const translationInstructions = 'Conversational Bengali. Preserve jokes and adapt idioms.';
  const result = restoreDubDraft(JSON.stringify({ ...defaults, translationInstructions }), defaults);
  expect(result?.translationInstructions).toBe(translationInstructions);
});
