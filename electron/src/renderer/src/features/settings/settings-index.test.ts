import en from '../../i18n/locales/en.json';
import { expect, it } from 'vitest';
import { SETTINGS_ENTRIES } from './settings-index';

const resolve = (key: string) =>
  key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);

it('only lists settings entries whose label exists in the English catalogue', () => {
  for (const entry of SETTINGS_ENTRIES) {
    expect(typeof resolve(entry.label), entry.label).toBe('string');
    expect(entry.to.startsWith('/settings/')).toBe(true);
  }
});
