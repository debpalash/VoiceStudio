import { describe, expect, test } from 'vitest';
import { MAX_MSG_CHARS, composeBugReportUrl } from './bugReportDocument';

function issueParams(opts) {
  return new URL(composeBugReportUrl(opts)).searchParams;
}
const issueBody = (opts) => issueParams(opts).get('body');

describe('backend exception class', () => {
  test('taken from errorClass on the error', () => {
    expect(issueBody({ error: { message: 'm', errorClass: 'ValueError' } })).toMatch(
      /^Backend error class: ValueError$/m,
    );
  });
  test('falls back to the parsed 500 body', () => {
    const error = { message: 'm', payload: { error_class: 'TimeoutError' } };
    expect(issueBody({ error })).toContain('Backend error class: TimeoutError');
  });
  test('error_type outranks the taxonomy label', () => {
    const error = { message: 'm', payload: { error_type: 'MemoryError', error_class: 'UNKNOWN' } };
    const body = issueBody({ error });
    expect(body).toContain('Backend error class: MemoryError');
    expect(body).not.toContain('UNKNOWN');
  });
  test('errorClass outranks anything in the payload', () => {
    const error = { message: 'm', errorClass: 'Own', payload: { error_type: 'Other' } };
    const body = issueBody({ error });
    expect(body).toContain('Own');
    expect(body).not.toContain('Other');
  });
  test('absent or malformed values print nothing', () => {
    const variants = [
      {},
      { errorClass: 5 },
      { errorClass: ['x'] },
      { payload: { error_type: false, error_class: {} } },
      { payload: { error_type: '' } },
    ];
    for (const extra of variants) {
      expect(issueBody({ error: { message: 'm', ...extra } })).not.toContain('Backend error class');
    }
  });
  test('home-directory paths inside the class are scrubbed', () => {
    const body = issueBody({ error: { message: 'm', errorClass: 'Boom/Users/bob/key' } });
    expect(body).not.toContain('/Users/bob');
  });
});

describe('failure topic', () => {
  test('flat payload value is shown', () => {
    const body = issueBody({
      error: { message: 'm', payload: { docs_topic: 'MODEL_CACHE_CORRUPT' } },
    });
    expect(body).toContain('MODEL_CACHE_CORRUPT');
  });
  test('nested detail value is shown', () => {
    const error = { message: 'm', payload: { detail: { docs_topic: 'NO_AUDIO_TRACK' } } };
    expect(issueBody({ error })).toContain('NO_AUDIO_TRACK');
  });
  test('non-string topics are skipped', () => {
    const body = issueBody({
      error: { message: 'm', payload: { docs_topic: 3, detail: 'plain' } },
    });
    expect(body).not.toContain('Backend failure topic');
  });
});

describe('rest of the document', () => {
  test('user paths in the message are shortened and seed the title', () => {
    const params = issueParams({ error: { message: 'missing /Users/carol/take1.wav' } });
    expect(params.get('body')).toContain('missing ~/take1.wav');
    expect(params.get('title')).toBe('[Bug] missing ~/take1.wav');
  });
  test('no error means no error section', () => {
    const body = issueBody({});
    expect(body).not.toContain('## Error');
    expect(body).toContain('## Environment');
  });
});

test('opens a report when a truncated message ends in half an emoji', () => {
  const message = `${'x'.repeat(MAX_MSG_CHARS - 1)}\u{1F600} tail`;
  expect(() => composeBugReportUrl({ error: new Error(message) })).not.toThrow();
  expect(() => composeBugReportUrl({ title: 'lone \uD83D' })).not.toThrow();
});
