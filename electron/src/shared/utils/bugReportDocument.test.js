import { describe, expect, test } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
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

describe('credential query privacy in the live report builder', () => {
  test.each([
    'pin',
    '%70in',
    'p%69%6e',
    'api%5Fkey',
    '%74oken',
    'pass%77ord',
    'auth%2Dtoken',
    '%61%63%63%65%73%73%5F%74%6F%6B%65%6E',
  ])('redacts %s from both error titles and crash-log bodies', (name) => {
    const raw = `Request: http://h/?${name}=481516&view=models&page=2`;
    const params = issueParams({ error: { message: raw }, crashSection: [raw] });
    for (const field of ['title', 'body']) {
      expect(params.get(field)).not.toContain('481516');
      expect(params.get(field)).toContain(`${name}=***REDACTED***`);
    }
    expect(params.get('body')).toContain('view=models');
    expect(params.get('body')).toContain('page=2');
  });

  test('keeps ordinary parameters and does not decode names twice', () => {
    const raw = 'http://192.0.2.1/?spin=481516&pin_code=481516&key=short&%2570in=481516&x=%GG';
    expect(issueBody({ crashSection: [raw] })).toContain(raw);
  });

  test('redacts every duplicate credential, including short named API keys', () => {
    const raw = 'http://192.0.2.1/?pin=481516&%70in=151623&api_key=short&view=models';
    const body = issueBody({ crashSection: [raw] });
    for (const secret of ['481516', '151623', 'short']) expect(body).not.toContain(secret);
    expect(body).toContain('view=models');
  });

  test.each([
    "pass%77ord='orchid-river-72'",
    'pass%77ord="orchid-river-72"',
    "password='orchid-river-72'",
    "pass%77ord='orchid-river-72",
    "pass%77ord='prefix\\'orchid-river-72'",
  ])('redacts the entire quoted credential %s', (query) => {
    const raw = `Request: http://192.0.2.1/?${query}&view=models`;
    const params = issueParams({ error: { message: raw }, crashSection: [raw] });
    expect(params.get('title')).not.toContain('orchid-river-72');
    expect(params.get('body')).not.toContain('orchid-river-72');
    expect(params.get('body')).toContain('***REDACTED***');
  });

  test('keeps quotes surrounding an unquoted URL credential', () => {
    expect(issueBody({ crashSection: ["Request: 'http://h/?pin=1234' next"] })).toContain(
      "Request: 'http://h/?pin=***REDACTED***' next",
    );
  });

  test.each(
    [
      ['password', 'PRIVATE_FIXTURE', '"'],
      ['%70in', '482916', '"'],
      ['password', 'PRIVATE_FIXTURE', "'"],
      ['%70in', '482916', "'"],
    ].flatMap((row) => [1, 3, 7].map((depth) => [...row, depth])),
  )('redacts log-escaped %s (%s, quote %s, depth %s)', (name, secret, quote, depth) => {
    const marker = '\\'.repeat(depth) + quote;
    const raw = `https://h/?${name}=${marker}${secret}${marker}&x=1`;
    const params = issueParams({ error: { message: raw }, crashSection: [raw] });
    expect(params.get('title')).not.toContain(secret);
    expect(params.get('body')).not.toContain(secret);
    expect(params.get('body')).toContain(`${name}=***REDACTED***&x=1`);
  });

  test.each(
    [
      ['api_key', "'"],
      ['api%5Fkey', "'"],
      ['api_key', '"'],
      ['api%5Fkey', '"'],
    ].flatMap((row) => [false, true].map((leading) => [...row, leading])),
  )(
    'redacts a quote inside the raw URL credential %s (%s, leading quoted %s)',
    (name, quote, leading) => {
      const prefix = leading ? `${quote}prefix${quote}` : 'prefix';
      const raw = `https://h/?${name}=${prefix}${quote}PRIVATE_FIXTURE${quote}&x=1`;
      const params = issueParams({ error: { message: raw }, crashSection: [raw] });
      expect(params.get('title')).not.toContain('PRIVATE_FIXTURE');
      expect(params.get('body')).not.toContain('PRIVATE_FIXTURE');
      expect(params.get('body')).toContain('&x=1');
    },
  );

  test.each([
    "Request: 'http://h/?pin=1234', status=500\nTraceback: decoder failed",
    '{"request":"http://h/?pin=1234"}\nTraceback: decoder failed',
    '{"request":"http://h/?pin=1234","status":500}\nTraceback: decoder failed',
  ])('preserves the comma/brace-delimited log tail for %s', (raw) => {
    const params = issueParams({ error: { message: raw }, crashSection: [raw] });
    expect(params.get('title')).not.toContain('1234');
    expect(params.get('body')).toContain(raw.replace('1234', '***REDACTED***'));
  });

  test('an outer URL quote does not expose internal credential punctuation', () => {
    const raw =
      "Request: 'https://h/?api_key=prefix',PRIVATE_FIXTURE'&x=1', status=500\nTraceback: decoder failed";
    const params = issueParams({ error: { message: raw }, crashSection: [raw] });
    expect(params.get('title')).not.toContain('PRIVATE_FIXTURE');
    expect(params.get('body')).not.toContain('PRIVATE_FIXTURE');
    for (const piece of ['&x=1', 'status=500', 'Traceback: decoder failed'])
      expect(params.get('body')).toContain(piece);
  });

  test('finishes malformed-query scrubbing within a process bound', () => {
    const source = pathToFileURL(resolve('src/shared/utils/scrub.js')).href;
    const script = `import {scrubText} from ${JSON.stringify(source)};
      for (const value of ['?'.repeat(100000),'&'+'?!'.repeat(50000)]) {
        if (scrubText(value)!==value) throw new Error('benign input changed');
      }`;
    execFileSync(process.execPath, ['--input-type=module', '-e', script], { timeout: 5000 });
  }, 10000);
});
