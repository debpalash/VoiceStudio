import { expect, it } from 'vitest';

const rendererSources = import.meta.glob('../../**/*.tsx', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>;

/**
 * Base UI's `<SelectValue />` renders the selected item's label only when the
 * root `<Select>` has `items`; without it the trigger shows the raw value, so
 * sentinels (`__default__`, `*`) and ids leak into the UI until opened.
 */
it('gives every Select with a bare SelectValue the items that label its value', () => {
  const offenders: string[] = [];
  for (const [path, source] of Object.entries(rendererSources)) {
    if (path.endsWith('.test.tsx') || path.endsWith('/components/ui/select.tsx')) continue;
    for (const match of source.matchAll(/<Select(?=[\s>])/g)) {
      const start = match.index;
      const end = source.indexOf('</Select>', start);
      const trigger = source.indexOf('<SelectTrigger', start);
      if (end < 0 || trigger < 0 || trigger > end) continue;
      if (!source.slice(start, end).includes('<SelectValue />')) continue;
      if (!/\bitems=\{/.test(source.slice(start, trigger))) {
        offenders.push(`${path}:${source.slice(0, start).split('\n').length}`);
      }
    }
  }
  expect(offenders).toEqual([]);
});
