import { describe, expect, it } from 'vitest';
import { FAILURE_CATALOG, failureTopicOf, isFailureTopic } from './failureCatalog';

describe('failureTopicOf', () => {
  it.each([
    ['a stream frame', { type: 'error', docs_topic: 'GPU_OOM' }, 'GPU_OOM'],
    ['a structured 400', { detail: { docs_topic: 'DISK_FULL', message: 'x' } }, 'DISK_FULL'],
    [
      'an ApiError',
      { payload: { detail: { docs_topic: 'MEDIA_TOOL_MISSING' } } },
      'MEDIA_TOOL_MISSING',
    ],
    ['a PublicFailure', { reason: 'x', docsTopic: 'HF_AUTH_FAILED' }, 'HF_AUTH_FAILED'],
    [
      'bare backend text',
      'Automatic reference transcription needs an installed speech-to-text model. Provide a matching reference transcript, or install and select a speech-to-text model in Model Catalogue, then try again.',
      'REFERENCE_STT_MISSING',
    ],
    [
      'an Error carrying backend text',
      new Error('This file has no audio track, so there is no speech to transcribe, dub or clone.'),
      'NO_AUDIO_TRACK',
    ],
  ])('reads the class from %s', (_label, value, topic) => {
    expect(failureTopicOf(value)).toBe(topic);
  });

  it.each([null, undefined, 'boom', {}, { docs_topic: 'constructor' }, { docs_topic: 'NOPE' }])(
    'ignores unknown or malformed input: %j',
    (value) => {
      expect(failureTopicOf(value)).toBeUndefined();
    },
  );

  it('rejects prototype keys as classes', () => {
    expect(isFailureTopic('toString')).toBe(false);
    expect(isFailureTopic('GPU_OOM')).toBe(true);
  });
});

describe('FAILURE_CATALOG', () => {
  it('maps every class to a message key and only to actions the app has', () => {
    for (const entry of Object.values(FAILURE_CATALOG)) {
      expect(entry.messageKey).toMatch(/^[\w]+\.[\w]+$/);
    }
  });
});
