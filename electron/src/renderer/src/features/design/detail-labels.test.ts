import { describe, expect, it } from 'vitest';
import { PRESETS } from '@shared/utils/constants';
import { DETAIL_VALUES, detailOptionKey } from './detail-labels';

const catalogs = import.meta.glob<Record<string, unknown>>('../../i18n/locales/*.json', {
  eager: true,
  import: 'default',
});
const HAN = /\p{Script=Han}/u;
const lookup = (catalog: Record<string, unknown>, key: string) =>
  key
    .split('.')
    .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], catalog);

describe('design detail labels', () => {
  it('keeps the model token but shows a translated label for a dialect', () => {
    expect(detailOptionKey('四川话')).toBe('clone.dialect_sichuan');
    expect(detailOptionKey('british accent')).toBe('clone.opt_british_accent');
    expect(detailOptionKey('middle-aged')).toBe('clone.opt_middle_aged');
    expect(detailOptionKey('Auto')).toBe('clone.auto');
  });

  it.each(Object.entries(catalogs))(
    '%s labels every detail and starting point',
    (file, catalog) => {
      const keys = [
        ...DETAIL_VALUES.map(detailOptionKey),
        ...PRESETS.map((preset) => `clone.preset_${preset.id}`),
      ];
      for (const key of keys) {
        const label = lookup(catalog, key);
        expect(typeof label, `${file} ${key}`).toBe('string');
        // Only the Chinese and Japanese catalogs may write these labels in Han script.
        if (!/(zh-|ja)/.test(file)) expect(HAN.test(label as string), `${file} ${key}`).toBe(false);
      }
    },
  );
});
