import { BUILT_IN_THEMES, getThemeColorsForAppearance, type ThemeAppearance } from './t3-palettes';
import { TAURI_THEME } from './tauri-palette';
export const AVAILABLE_PALETTES = [...BUILT_IN_THEMES, TAURI_THEME];
export type PaletteId = 'default' | (typeof BUILT_IN_THEMES)[number]['id'];
export type ThemeMode = ThemeAppearance | 'system';
export interface ThemePreferences {
  mode: ThemeMode;
  light: PaletteId;
  dark: PaletteId;
}
export const THEME_STORAGE_KEY = 'voicestudio.theme.v2';
export function parseThemePreferences(
  raw: string | null,
  legacy: string | null = null,
): ThemePreferences {
  let value: Partial<ThemePreferences> = {};
  try {
    const parsed = JSON.parse(raw ?? '{}');
    if (parsed && typeof parsed === 'object') value = parsed;
  } catch {
    /* Fall back to the previous appearance. */
  }
  const aliases: Record<string, string> = {
    't3-chat': 'signal',
    grove: 'canopy',
    ocean: 'current',
    ember: 'hearth',
    iris: 'orchid',
    'voicestudio-classic': 'heritage',
  };
  const palette = (value: unknown) => {
    const id = typeof value === 'string' ? (aliases[value] ?? value) : value;
    return typeof id === 'string' &&
      (id === 'default' || AVAILABLE_PALETTES.some((theme) => theme.id === id))
      ? id
      : TAURI_THEME.id;
  };
  return {
    mode: ['light', 'dark', 'system'].includes(value.mode ?? '')
      ? value.mode!
      : legacy === 'light'
        ? 'light'
        : 'dark',
    light: palette(value.light),
    dark: palette(value.dark),
  };
}
export function resolveTheme(preferences: ThemePreferences, systemDark: boolean) {
  const appearance: ThemeAppearance =
    preferences.mode === 'system' ? (systemDark ? 'dark' : 'light') : preferences.mode;
  return { appearance, paletteId: preferences[appearance] };
}
export function paletteColors(id: string, appearance: ThemeAppearance) {
  const theme = AVAILABLE_PALETTES.find((item) => item.id === id);
  return theme ? getThemeColorsForAppearance(theme, appearance) : null;
}
export function colorVariable(role: string) {
  return (
    '--app-theme-' +
    (role === 'terminalSelection'
      ? 'terminal-selection-background'
      : role.replace(/[A-Z]/g, (letter) => '-' + letter.toLowerCase()))
  );
}
