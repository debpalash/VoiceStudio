import assert from 'node:assert/strict';
import test from 'node:test';
import { buildPastePlan, detectPasteMode } from '../../electron/src/shared/utils/pasteTranslations.js';

for (const separator of [',', '.']) {
  test(`long-hour subtitle paste routes to timestamp matching (${separator})`, () => {
    const text = `1\n100:00:01${separator}000 --> 100:00:02${separator}000\nTranslated cue\n`;
    assert.equal(detectPasteMode(text), 'timestamped');
    const plan = buildPastePlan(text, [{ id: 'cue', start: 360001, end: 360002, text: 'Original' }], {
      cues: [{ start: 360001, end: 360002, text: 'Translated cue' }],
    });
    assert.equal(plan.mode, 'timestamped');
    assert.equal(plan.rows[0].after, 'Translated cue');
    assert.equal(plan.matchedCount, 1);
  });
}


test('plain translations that begin with years keep those years', () => {
  const text = '2024 was a good year\n2025 was better';
  const segments = [
    { id: 'a', start: 0, end: 1, text: 'Original A' },
    { id: 'b', start: 1, end: 2, text: 'Original B' },
  ];
  const plan = buildPastePlan(text, segments);
  assert.equal(plan.mode, 'plain');
  assert.deepEqual(plan.rows.map(({ after }) => after), ['2024 was a good year', '2025 was better']);
  for (const numbered of ['1 First\n2 Second', 'Here are the translations:\n1 First\n2 Second', '1. First\n2. Second', '[1] - First\n[2] - Second']) {
    const plan = buildPastePlan(numbered, segments);
    assert.equal(plan.mode, 'numbered');
    assert.deepEqual(plan.rows.map(({ after }) => after), ['First', 'Second']);
  }
  const mixedNumbered = buildPastePlan('1. First\n2 Second\n3. Third', [
    ...segments, { id: 'c', start: 2, end: 3, text: 'Original C' },
  ]);
  assert.deepEqual(mixedNumbered.rows.map(({ after }) => after), ['First', 'Second', 'Third']);
  const mixed = buildPastePlan('1. First\n2024 was a good year\n2. Second', segments);
  assert.deepEqual(mixed.rows.map(({ after }) => after), ['First 2024 was a good year', 'Second']);
});
