import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isAllowedRequest, STAR_COUNT_URL } from './local-first.mjs';

const allowed = [
  'app://-/index.html',
  'file:///x',
  'data:,x',
  'blob:app://-/u',
  'devtools://devtools/x',
  'http://127.0.0.1:3900/health',
  'http://localhost:49306/api',
  'http://[::1]:1/',
  'ws://127.0.0.1:41939/ws/events',
  'ws://localhost:3900/ws/events',
];
const rejected = [
  STAR_COUNT_URL,
  'https://app.supermetry.com/api/public/v1/events',
  'https://huggingface.co/',
  'http://127.0.0.1.evil.com/',
  'http://localhost.evil.com/',
  'ws://example.com',
  'not a url',
];

for (const url of allowed) {
  test(`allows ${url}`, () => assert.equal(isAllowedRequest(url), true));
}
for (const url of rejected) {
  test(`rejects ${url}`, () => assert.equal(isAllowedRequest(url), false));
}
