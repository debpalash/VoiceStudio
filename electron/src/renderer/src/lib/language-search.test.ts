import { describe, expect, it } from 'vitest';
import { layoutMenu, MORE_ROW, rankOptions, type SearchOption } from './language-search';

const labels = { recent: 'Recent', available: 'Available', unavailable: 'Not supported' };
const option = (value: string, extra: Partial<SearchOption> = {}): SearchOption => ({
  value,
  label: value,
  search: [value.toLowerCase()],
  ...extra,
});
const layout = (
  options: SearchOption[],
  { query = '', recents = [] as string[], columns = 1, expanded = false } = {},
) =>
  layoutMenu({
    sorted: rankOptions(options, query, recents),
    query,
    recents,
    columns,
    expanded,
    labels,
  });
const shape = (rows: ReturnType<typeof layout>['rows']) =>
  rows.map((row) =>
    row.kind === 'items'
      ? row.items.map((item) => item.value).join('+')
      : row.kind === 'more'
        ? `more:${row.hidden}${row.expanded ? ':open' : ''}`
        : `${row.kind}:${row.label}`,
  );

describe('layoutMenu', () => {
  const options = [
    option('Auto', { pinned: true }),
    option('English'),
    option('German', { disabled: true }),
    option('Japanese'),
    option('Zulu', { disabled: true }),
  ];

  it('collapses unsupported languages behind one row and keeps it keyboard reachable', () => {
    const result = layout(options);
    expect(shape(result.rows)).toEqual([
      'Auto',
      'header:Available',
      'English',
      'Japanese',
      'more:2',
    ]);
    expect(result.navigation).toEqual(['Auto', 'English', 'Japanese', MORE_ROW]);
  });

  it('reveals unsupported languages when expanded, never as keyboard targets', () => {
    const result = layout(options, { expanded: true });
    expect(shape(result.rows).slice(-4)).toEqual([
      'more:2:open',
      'header:Not supported',
      'German',
      'Zulu',
    ]);
    expect(result.navigation).not.toContain('German');
    // The toggle is an option too: it takes a position and counts toward the set size.
    expect(result.size).toBe(6);
    const more = result.rows.find((row) => row.kind === 'more');
    expect(more && 'position' in more ? more.position : -1).toBe(3);
    const german = result.rows.find(
      (row) => row.kind === 'items' && row.items[0].value === 'German',
    );
    expect(german && 'start' in german ? german.start : -1).toBe(4);
  });

  it('shows unsupported languages without a toggle when it must not hide them', () => {
    const result = layoutMenu({
      sorted: rankOptions(options, '', []),
      query: '',
      recents: [],
      columns: 1,
      expanded: false,
      toggle: false,
      labels,
    });
    expect(shape(result.rows).slice(-3)).toEqual(['header:Not supported', 'German', 'Zulu']);
    expect(result.navigation).not.toContain(MORE_ROW);
  });

  it('keeps unsupported matches searchable without the toggle row', () => {
    const result = layout(options, { query: 'zu' });
    expect(shape(result.rows)).toEqual(['header:Not supported', 'Zulu']);
    expect(result.navigation).toEqual([]);
  });

  it('leads with recent enabled choices and leaves disabled recents in their section', () => {
    const result = layout(options, { recents: ['Japanese', 'German'] });
    expect(shape(result.rows)).toEqual([
      'Auto',
      'header:Recent',
      'Japanese',
      'header:Available',
      'English',
      'more:2',
    ]);
  });

  it('shows a plain list when every language is supported and nothing is recent', () => {
    const result = layout([option('Auto', { pinned: true }), option('English'), option('Hindi')]);
    expect(shape(result.rows)).toEqual(['Auto', 'English', 'Hindi']);
  });

  it('groups regional variants under their language at the first variant position', () => {
    const variants = [
      option('Polish'),
      option('Portuguese (Brazil)', { family: 'pt', familyLabel: 'Portuguese' }),
      option('Punjabi'),
      option('Portuguese (Portugal)', { family: 'pt', familyLabel: 'Portuguese' }),
      option('Russian'),
    ];
    const result = layout(variants, { columns: 2 });
    expect(shape(result.rows)).toEqual([
      'Polish',
      'family:Portuguese',
      'Portuguese (Brazil)+Portuguese (Portugal)',
      'Punjabi+Russian',
    ]);
    expect(result.navigation).toEqual([
      'Polish',
      'Portuguese (Brazil)',
      'Portuguese (Portugal)',
      'Punjabi',
      'Russian',
    ]);
    expect(result.visible.map((item) => item.value)).toEqual(result.navigation);
  });

  it('does not add a group heading for a lone variant', () => {
    const result = layout([
      option('Portuguese (Portugal)', { family: 'pt', familyLabel: 'Portuguese' }),
      option('Russian'),
    ]);
    expect(shape(result.rows)).toEqual(['Portuguese (Portugal)', 'Russian']);
  });
});
