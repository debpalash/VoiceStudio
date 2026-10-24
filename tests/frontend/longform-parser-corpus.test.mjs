import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
// Resolve the renderer's extensionless dependency without its bundler.
const moduleUrl = new URL('../../electron/src/shared/utils/longformParser.js', import.meta.url);
const dependencyUrl = new URL('../../electron/src/shared/utils/ssmlLite.js', import.meta.url);
const source = readFileSync(moduleUrl, 'utf8').replace("'./ssmlLite'", JSON.stringify(dependencyUrl.href));
const { parseScriptToSpans } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const cases = JSON.parse(readFileSync(new URL('../fixtures/longform_parser_cases.json', import.meta.url), 'utf8'));
for (const item of cases) {
  test(`longform shared corpus: ${item.name}`, () => {
    assert.deepEqual(parseScriptToSpans(item.input, {
      defaultVoice: item.default_voice,
      defaultSpeed: item.default_speed ?? null,
    }), item.expected);
  });
}

const storyUrl = new URL('../../electron/src/shared/utils/storyTokens.js', import.meta.url);
const longformDataUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const storySource = readFileSync(storyUrl, 'utf8').replace("'./longformParser'", JSON.stringify(longformDataUrl));
const { parseStoryText } = await import(`data:text/javascript;base64,${Buffer.from(storySource).toString('base64')}`);
for (const length of [308, 400]) for (const unit of ['', 'ms', 's']) {
  test(`Stories preview and render clamp ${length}-digit pause in ${unit || 'bare milliseconds'}`, () => {
    const text = `Before [pause ${'9'.repeat(length)}${unit}] after`;
    const preview = parseStoryText(text).filter(event => event.type === 'pause');
    const rendered = parseScriptToSpans(text).flatMap(chapter => chapter.spans).filter(span => span.pause_ms_after > 0);
    assert.deepEqual(preview.map(event => event.seconds), [10]);
    assert.deepEqual(rendered.map(span => span.pause_ms_after), [10000]);
  });
}
