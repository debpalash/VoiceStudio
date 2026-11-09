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
