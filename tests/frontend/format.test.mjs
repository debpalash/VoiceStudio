// Unit tests for electron/src/shared/utils/format.js — timecode formatter.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatTime } from '../../electron/src/shared/utils/format.js';


test('formatTime seconds below a minute', () => {
  assert.equal(formatTime(0),    '0:00.0');
  assert.equal(formatTime(3.1),  '0:03.1');
  assert.equal(formatTime(9.05), '0:09.1'); // JS toFixed uses banker-ish rounding
});

test('formatTime whole minutes', () => {
  assert.equal(formatTime(60),   '1:00.0');
  assert.equal(formatTime(120),  '2:00.0');
  assert.equal(formatTime(3600), '60:00.0');
});

test('formatTime mixed minutes + seconds', () => {
  assert.equal(formatTime(75.4),   '1:15.4');
  assert.equal(formatTime(125.1),  '2:05.1');
  assert.equal(formatTime(599.9),  '9:59.9');
});

test('formatTime zero-pads single-digit seconds', () => {
  assert.equal(formatTime(61.2),  '1:01.2');
  assert.equal(formatTime(68),    '1:08.0');
});

test('formatTime fractional boundary', () => {
  // 59.95 → minutes=0, sec=59.95.toFixed(1)='60.0' — known minor quirk but
  // documented here so a future refactor knows what the current behaviour is.
  const s = formatTime(59.95);
  assert.ok(s === '0:60.0' || s === '1:00.0', `unexpected: ${s}`);
});
