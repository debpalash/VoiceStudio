import { BUILT_IN_THEMES } from '@/lib/themes/t3-palettes';
import { modelFamilies, type ModelFamily } from './model-family';

type Translate = (key: string) => string;

export const preferenceSections = ['general', 'appearance'] as const;
export type PreferenceSection = (typeof preferenceSections)[number];

export const preferenceFields: Record<PreferenceSection, readonly string[]> = {
  general: ['language', 'review_mode'],
  appearance: ['theme', 'font', 'ui_scale', 'glass', 'sidebar_expanded'],
};

/** The i18n key naming a General / Appearance field. */
export const fieldKey = (key: string) =>
  key === 'glass'
    ? 'themeAppearance.glass'
    : key === 'language' || key === 'review_mode'
      ? `settings.${key}`
      : `preferences.${key}`;

/** Extra words a field answers to, so "dark" finds Appearance → Theme. */
const fieldAliases: Record<string, readonly string[]> = {
  theme: ['themeAppearance.light', 'themeAppearance.dark', 'themeAppearance.system'],
};
/** Palette names are brand names shown untranslated in the picker. */
const fieldLiterals: Record<string, readonly string[]> = {
  theme: BUILT_IN_THEMES.map((theme) => theme.label),
};

/** Settings pages always listed in the nav, with the words that find them. */
export const pinnedPages = {
  models: ['modelSettings.models', ...modelFamilies.map((family) => 'engineSidebar.' + family)],
  media: ['settings.audio_tools', 'settings.ffmpeg'],
  logs: ['settings.logs'],
} as const;
export type PinnedPage = keyof typeof pinnedPages;

export function normalizeSettingsQuery(query: string) {
  return query.trim().toLocaleLowerCase();
}

/** True when the needle is empty or any key's translation contains it. */
export function keysMatch(t: Translate, keys: readonly string[], needle: string) {
  return !needle || keys.some((key) => t(key).toLocaleLowerCase().includes(needle));
}

export function fieldMatches(t: Translate, field: string, needle: string) {
  return (
    keysMatch(t, [fieldKey(field), ...(fieldAliases[field] ?? [])], needle) ||
    (fieldLiterals[field] ?? []).some((name) => name.toLocaleLowerCase().includes(needle))
  );
}

export function sectionMatches(t: Translate, section: PreferenceSection, needle: string) {
  return (
    keysMatch(t, [`preferences.${section}`], needle) ||
    preferenceFields[section].some((field) => fieldMatches(t, field, needle))
  );
}

/** Model families to list under Models: all on a Models page, else those the query names. */
export function matchingFamilies(t: Translate, needle: string, onModels: boolean): ModelFamily[] {
  if (!needle) return onModels ? [...modelFamilies] : [];
  const named = modelFamilies.filter((family) => keysMatch(t, ['engineSidebar.' + family], needle));
  return named.length || !onModels ? named : [...modelFamilies];
}
