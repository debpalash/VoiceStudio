import type { TFunction } from 'i18next';
import { CATEGORIES, CHINESE_DIALECT_IDS } from '@shared/utils/constants';

/**
 * The i18n key for a design detail. Details are model vocabulary (English
 * tokens, Chinese-dialect tokens) and are sent as they are; only their label
 * is translated, so no raw token reaches the screen in another language.
 */
export function detailOptionKey(value: string): string {
  if (value === 'Auto') return 'clone.auto';
  const dialect = CHINESE_DIALECT_IDS[value];
  if (dialect) return `clone.dialect_${dialect}`;
  return `clone.opt_${value.replaceAll(' ', '_').replaceAll('-', '_')}`;
}

/** Every detail value the Details section can show, for catalog checks. */
export const DETAIL_VALUES: readonly string[] = Object.values(CATEGORIES).flat();

export function detailLabel(t: TFunction, value: string): string {
  return t(detailOptionKey(value), { defaultValue: value });
}

/** Preset labels carry a leading emoji in older catalogs; chips show icons instead. */
export function plainLabel(label: string): string {
  return label.replace(/[\p{Extended_Pictographic}️]/gu, '').trim();
}
