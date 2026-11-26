import { expect, it } from 'vitest';
import { dubGuideState } from './dub-project-guide';
import { dubSession } from './dub-session';
it('requires the voice after importing target-language SRT, without asking for transcription or translation', () => {
  const state = dubGuideState(
    {
      ...dubSession.state,
      jobId: 'job',
      phase: 'editing',
      tracks: [],
      subtitleTargetLang: 'en',
      sourceLanguage: 'es',
      target: 'English',
      segments: [{ id: 'one', start: 0, end: 2, text: 'Hello', text_original: 'Hello' }],
    },
    'en',
  );
  expect(state.textReady).toBe(true);
  expect(state.voiceReady).toBe(false);
  expect(state.nextStep).toBe('voice');
});
