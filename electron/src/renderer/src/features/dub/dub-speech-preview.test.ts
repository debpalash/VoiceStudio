import { expect, it } from 'vitest';
import { segmentAuditionRequest } from './dub-speech-preview';
import type { DubSession } from './dub-session';

const segment = {
  id: 'a',
  start: 1,
  end: 3,
  text: 'Hello',
  text_original: 'Hola',
  profile_id: 'voice',
};
const session = {
  jobId: 'job',
  target: 'English',
  timingStrategy: 'strict_slot',
  segments: [segment],
} as DubSession;
const base = { jobId: 'job', segment, session, index: 0, code: 'en' };

it('auditions a line with no saved take instead of asking for a missing file', () => {
  const request = segmentAuditionRequest({ ...base, saved: false });
  expect(request.synthesizes).toBe(true);
  expect(request.path).toBe('/dub/preview-segment/job');
  expect(request.init.method).toBe('POST');
  expect(JSON.parse(request.init.body as string)).toMatchObject({
    segment_id: 'a',
    text: 'Hello',
    profile_id: 'voice',
    language: 'en',
    duration: 2,
  });
});

it('reads saved takes back without synthesizing', () => {
  expect(segmentAuditionRequest({ ...base, saved: true })).toEqual({
    path: '/dub/preview/job/0?lang=en',
    init: {},
    synthesizes: false,
  });
  expect(
    segmentAuditionRequest({ ...base, saved: true, savedUrl: '/dub/speech-preview/x' }).path,
  ).toBe('/dub/speech-preview/x');
});
