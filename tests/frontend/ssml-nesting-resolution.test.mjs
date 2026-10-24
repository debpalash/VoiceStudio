import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';

// Resolve the renderer's bundler extensionless import; run its original code.
const source = fs.readFileSync(new URL('../../electron/src/shared/utils/longformParser.js', import.meta.url), 'utf8')
  .replace("'./ssmlLite'", JSON.stringify(new URL('../../electron/src/shared/utils/ssmlLite.js', import.meta.url).href));
const { parseScriptToSpans } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const cases = JSON.parse(fs.readFileSync(new URL('../fixtures/longform_parser_cases.json', import.meta.url), 'utf8'));

test('editor preserves the shared longform corpus including non-top and unmatched closes', () => {
  for (const c of cases) {
    assert.deepEqual(parseScriptToSpans(c.input, { defaultVoice: c.default_voice, defaultSpeed: c.default_speed }), c.expected, c.name);
  }
});

test('interleaved deeply nested text resolves within a token-scaled work bound', async () => {
  const depth = 4000;
  const original = fs.readFileSync(new URL('../../electron/src/shared/utils/ssmlLite.js', import.meta.url), 'utf8');
  // Count visits in the resolution loop without changing its control flow.
  // Match either the previous full-stack walk or the fixed vocabulary walk.
  const loop = /for \(const name of (?:stack|\[[^\n]+\])\) \{/g;
  assert.equal([...original.matchAll(loop)].length, 1);
  const instrumented = 'let resolutionWork = 0;\n' + original.replace(loop, '$&\n resolutionWork++;')
    + '\nexport function resolutionVisits() { return resolutionWork; }\n';
  const { parseSsmlLite, resolutionVisits } = await import(`data:text/javascript;base64,${Buffer.from(instrumented).toString('base64')}`);
  assert.deepEqual(parseSsmlLite('[slow]x'.repeat(depth) + '[/slow]'.repeat(depth)), [
    { text: 'x'.repeat(depth), speed: 0.85, spell: false, emphasis: false },
  ]);
  assert.ok(resolutionVisits() <= 3 * (2 * depth + 1));
});
