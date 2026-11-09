import { expect, it } from 'vitest';
import { countByFilter, matchesFilter } from './library-filter';

it('keeps History takes, voices, transcripts and exports out of All projects', () => {
  expect(matchesFilter('dub', 'all')).toBe(true);
  expect(matchesFilter('stories', 'all')).toBe(true);
  expect(matchesFilter('audiobooks', 'all')).toBe(true);
  for (const kind of ['history', 'profiles', 'transcripts', 'exports'] as const) {
    expect(matchesFilter(kind, 'all')).toBe(false);
    expect(matchesFilter(kind, kind)).toBe(true);
  }
});

it('counts All projects from projects only', () => {
  const counts = countByFilter(['dub', 'stories', 'history', 'history', 'profiles']);
  expect(counts.all).toBe(2);
  expect(counts.history).toBe(2);
  expect(counts.exports).toBe(0);
});
