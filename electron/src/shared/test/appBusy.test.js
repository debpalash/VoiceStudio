/**
 * `isAppBusy` decides whether it is safe to throw away the running process.
 *
 * The bug it replaces: the updater asked `dubStep === 'generating'` and nothing
 * else, so installing an update relaunched the app — discarding the work —
 * during a dub upload, a transcription, a translation, an export, or a
 * standalone synth. Two copies of that check existed and could drift.
 *
 * These cases are the contract. Adding a long-running operation to the app
 * means adding it here too.
 */
import { describe, it, expect } from 'vitest';
import { isAppBusy } from '../utils/appBusy';

const idle = { dubStep: 'idle', stage: 'idle', ttsInflight: 0 };

describe('isAppBusy', () => {
  it.each([
    ['dub upload', { dubStep: 'uploading' }],
    ['dub transcription', { dubStep: 'transcribing' }],
    ['dub synth', { dubStep: 'generating' }],
    ['dub being stopped', { dubStep: 'stopping' }],
    ['ASR model load', { stage: 'loading-model' }],
    ['dictation capture', { stage: 'recording' }],
    ['transcription', { stage: 'transcribing' }],
    ['translation', { stage: 'translating' }],
    ['pill-tracked synth', { stage: 'generating' }],
    ['export encode', { stage: 'exporting' }],
    ['refine pass', { stage: 'refining' }],
    ['standalone TTS synth', { ttsInflight: 1 }],
    ['overlapping synths', { ttsInflight: 3 }],
  ])('is busy during %s', (_label, state) => {
    expect(isAppBusy({ ...idle, ...state })).toBe(true);
  });

  it.each([
    ['nothing running', {}],
    // Waiting on the user is not an operation. Counting it would block updates
    // for as long as a transcript tab stays open.
    ['an open transcript', { dubStep: 'editing' }],
    ['a finished dub', { dubStep: 'done' }],
    ['a finished pill', { stage: 'done' }],
    ['a failed pill', { stage: 'error' }],
  ])('is not busy with %s', (_label, state) => {
    expect(isAppBusy({ ...idle, ...state })).toBe(false);
  });

  it('treats a missing state as not busy rather than throwing', () => {
    expect(isAppBusy(undefined)).toBe(false);
    expect(isAppBusy(null)).toBe(false);
    expect(isAppBusy({})).toBe(false);
  });
});
