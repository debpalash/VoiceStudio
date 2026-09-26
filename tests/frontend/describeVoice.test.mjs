// Unit tests for the "describe your voice" (#317) frontend mapping glue:
// mergeDescribedAttrs projects the backend /design/describe response onto a
// fresh vdStates object, guarding against drift between backend tokens and
// the picker's CATEGORIES whitelist. Runs under node:test, no backend needed.

import { test } from 'node:test';
import assert from 'node:assert/strict';

const utilPath = new URL('../../electron/src/shared/utils/voiceInstruct.js', import.meta.url).pathname;
const constantsPath = new URL('../../electron/src/shared/utils/constants.js', import.meta.url).pathname;
const { mergeDescribedAttrs } = await import(utilPath);
const { CATEGORIES } = await import(constantsPath);

const ALL_CATS = Object.keys(CATEGORIES);

test('matched tokens are applied, unmatched categories reset to Auto', () => {
  const out = mergeDescribedAttrs({
    Gender: 'female', Age: 'elderly', EnglishAccent: 'british accent',
    Pitch: 'Auto', Style: 'Auto', ChineseDialect: 'Auto',
  });
  assert.equal(out.Gender, 'female');
  assert.equal(out.Age, 'elderly');
  assert.equal(out.EnglishAccent, 'british accent');
  assert.equal(out.Pitch, 'Auto');
  assert.equal(out.Style, 'Auto');
  assert.equal(out.ChineseDialect, 'Auto');
});

test('every CATEGORIES key is always present (full vdStates shape)', () => {
  const out = mergeDescribedAttrs({ Age: 'child' });
  assert.deepEqual(Object.keys(out).sort(), [...ALL_CATS].sort());
  for (const cat of ALL_CATS) {
    if (cat !== 'Age') assert.equal(out[cat], 'Auto');
  }
});

test('tokens not in the picker whitelist are dropped (backend drift guard)', () => {
  const out = mergeDescribedAttrs({
    Gender: 'robot',                 // not a CATEGORIES token
    Age: 'elderly',
    Pitch: 'subsonic pitch',         // not a CATEGORIES token
    UnknownCategory: 'whatever',     // category the picker doesn't know
  });
  assert.equal(out.Gender, 'Auto');
  assert.equal(out.Pitch, 'Auto');
  assert.equal(out.Age, 'elderly');
  assert.ok(!('UnknownCategory' in out));
});

test('retyping a description never leaks stale tokens (fresh object each time)', () => {
  // First description set Gender; second description has no gender — the
  // result must reset Gender to Auto rather than carrying the old value.
  const second = mergeDescribedAttrs({ Pitch: 'low pitch' });
  assert.equal(second.Gender, 'Auto');
  assert.equal(second.Pitch, 'low pitch');
});

test('empty / missing attrs yields all-Auto', () => {
  for (const input of [undefined, null, {}]) {
    const out = mergeDescribedAttrs(input);
    for (const cat of ALL_CATS) assert.equal(out[cat], 'Auto');
  }
});

test('#983 — a partial vdStates shape (as restored from a saved profile or '
  + 'localStorage) is completed to all 6 CATEGORIES keys', () => {
  // Mirrors the exact partial shape from issue #983: only Gender survives
  // (e.g. a design profile saved by an older client, or a hand-edited
  // payload), the other 5 category keys are simply absent from the object.
  // useProfiles.js/useAppData.js now run any restored vd_states through this
  // helper before calling setVdStates, so DesignMethodPanel's render never
  // sees an undefined category value.
  const out = mergeDescribedAttrs({ Gender: 'male' });
  assert.deepEqual(Object.keys(out).sort(), [...ALL_CATS].sort());
  assert.equal(out.Gender, 'male');
  for (const cat of ALL_CATS) {
    if (cat !== 'Gender') assert.equal(out[cat], 'Auto');
  }
});
