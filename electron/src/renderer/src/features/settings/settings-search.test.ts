import { expect, it } from 'vitest';
import en from '../../i18n/locales/en.json';
import {
  fieldMatches,
  keysMatch,
  matchingFamilies,
  normalizeSettingsQuery,
  pinnedPages,
  sectionMatches,
} from './settings-search';

const t = (key: string) =>
  String(
    key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en) ??
      key,
  );

it('lists Models, Audio tools and Logs only when the query matches them', () => {
  const needle = normalizeSettingsQuery('zzz');
  expect(keysMatch(t, pinnedPages.models, needle)).toBe(false);
  expect(keysMatch(t, pinnedPages.media, needle)).toBe(false);
  expect(keysMatch(t, pinnedPages.logs, needle)).toBe(false);
  expect(keysMatch(t, pinnedPages.logs, normalizeSettingsQuery('Logs'))).toBe(true);
  expect(keysMatch(t, pinnedPages.logs, '')).toBe(true);
});

it('finds Appearance → Theme by mode and palette names', () => {
  for (const query of ['dark', 'Light', 'theme', 'hearth']) {
    const needle = normalizeSettingsQuery(query);
    expect(fieldMatches(t, 'theme', needle), query).toBe(true);
    expect(sectionMatches(t, 'appearance', needle), query).toBe(true);
  }
  expect(sectionMatches(t, 'general', normalizeSettingsQuery('dark'))).toBe(false);
});

it('finds model families by name from anywhere in Settings', () => {
  expect(keysMatch(t, pinnedPages.models, normalizeSettingsQuery('dictation'))).toBe(true);
  expect(matchingFamilies(t, normalizeSettingsQuery('dictation'), false)).toEqual(['dictation']);
  expect(matchingFamilies(t, '', false)).toEqual([]);
  expect(matchingFamilies(t, '', true)).toHaveLength(6);
});
