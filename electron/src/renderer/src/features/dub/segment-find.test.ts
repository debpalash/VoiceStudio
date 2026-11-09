import { describe, expect, it } from 'vitest';
import {
  applyReplaceAll,
  collectMatches,
  nextMatchIndex,
  queryPattern,
  replaceAllInText,
  replaceCurrentMatch,
  textUpdates,
} from './segment-find';

const rows = [
  { id: 'a', text: 'Hello ACME city', text_original: 'Hello ACME town' },
  { id: 'b', text: 'acme later', text_original: 'acme later' },
  { id: 'c', text: 'Nothing here', text_original: 'ACME in the source' },
];

describe('collectMatches', () => {
  it('finds case-insensitive matches in spoken text and a distinct original', () => {
    const matches = collectMatches(rows, 'acme');
    expect(matches).toEqual([
      { id: 'a', field: 'text', start: 6, end: 10 },
      { id: 'a', field: 'text_original', start: 6, end: 10 },
      { id: 'b', field: 'text', start: 0, end: 4 },
      { id: 'c', field: 'text_original', start: 0, end: 4 },
    ]);
  });

  it('ignores blank queries, missing rows, and empty originals that match the spoken line', () => {
    expect(collectMatches(rows, '   ')).toEqual([]);
    expect(collectMatches([null, { id: 'z', text: 'ACME' }], 'acme')).toEqual([
      { id: 'z', field: 'text', start: 0, end: 4 },
    ]);
    expect(collectMatches([{ id: 'same', text: 'ACME', text_original: 'ACME' }], 'acme')).toEqual([
      { id: 'same', field: 'text', start: 0, end: 4 },
    ]);
  });

  it('treats regex metacharacters as literal text', () => {
    expect(queryPattern('a+b?')?.source).toBe('a\\+b\\?');
    expect(collectMatches([{ id: '1', text: 'cost is $5 (est.)' }], '$5 (est.)')).toEqual([
      { id: '1', field: 'text', start: 8, end: 17 },
    ]);
  });
});

describe('replace helpers', () => {
  it('replaces every spoken occurrence and leaves originals alone', () => {
    const { segments, count } = applyReplaceAll(rows, 'acme', 'Apex');
    expect(count).toBe(2);
    expect(segments.map((row) => row.text)).toEqual([
      'Hello Apex city',
      'Apex later',
      'Nothing here',
    ]);
    expect(segments[2].text_original).toBe('ACME in the source');
  });

  it('replaces only the current spoken match', () => {
    const matches = collectMatches(rows, 'acme');
    const spoken = matches.find((match) => match.field === 'text');
    expect(spoken).toBeTruthy();
    const next = replaceCurrentMatch(rows, spoken!, 'Apex');
    expect(next[0].text).toBe('Hello Apex city');
    expect(next[1].text).toBe('acme later');
  });

  it('refuses to rewrite original-only matches', () => {
    const original = collectMatches(rows, 'acme').find((match) => match.field === 'text_original');
    expect(replaceCurrentMatch(rows, original!, 'Apex')[2].text).toBe('Nothing here');
    expect(replaceCurrentMatch(rows, original!, 'Apex')[2].text_original).toBe(
      'ACME in the source',
    );
  });

  it('counts replacements inside one line', () => {
    expect(replaceAllInText('foo foo FOO', 'foo', 'bar')).toEqual({
      text: 'bar bar bar',
      count: 3,
    });
  });
});

describe('nextMatchIndex', () => {
  it('wraps forward and backward and rejects an empty list', () => {
    expect(nextMatchIndex(3, 2, 1)).toBe(0);
    expect(nextMatchIndex(3, 0, -1)).toBe(2);
    expect(nextMatchIndex(0, 0, 1)).toBe(-1);
  });
});

describe('textUpdates', () => {
  it('emits only segments whose spoken text changed', () => {
    const next = replaceCurrentMatch(rows, collectMatches(rows, 'acme')[0], 'Apex');
    expect(textUpdates(rows, next)).toEqual([{ id: 'a', text: 'Hello Apex city' }]);
  });
});
