import { expect, it } from 'vitest';

import { parseChapterBody, PAUSE_MAX_MS } from '../utils/longformParser';

it.each([[400, 'ms'], [307, 's']])('clamps a %s-digit %s pause', (digits, unit) => {
  const spans = parseChapterBody(`Before[pause ${'9'.repeat(digits)}${unit}]After`);
  expect(spans.map((span) => [span.text, span.pause_ms_after])).toEqual([
    ['Before', PAUSE_MAX_MS],
    ['After', 0],
  ]);
});
