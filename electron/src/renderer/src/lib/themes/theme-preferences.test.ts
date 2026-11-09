import { describe, expect, it } from 'vitest';
import { BUILT_IN_THEMES, THEME_COLOR_ROLES } from './t3-palettes';
import {
  AVAILABLE_PALETTES,
  colorVariable,
  paletteColors,
  parseThemePreferences,
  resolveTheme,
} from './theme-preferences';

describe('theme preferences', () => {
  it('preserves the old appearance and tolerates malformed saved preferences', () => {
    expect(parseThemePreferences('{', 'light')).toEqual({
      mode: 'light',
      light: 'default',
      dark: 'default',
    });
    expect(parseThemePreferences('{"mode":"invalid","light":"missing","dark":"current"}')).toEqual({
      mode: 'dark',
      light: 'default',
      dark: 'current',
    });
    expect(parseThemePreferences('null')).toEqual({
      mode: 'dark',
      light: 'default',
      dark: 'default',
    });
  });
  it('resolves independent halves when the operating system changes', () => {
    const preferences = parseThemePreferences(
      '{"mode":"system","light":"canopy","dark":"current"}',
    );
    expect(resolveTheme(preferences, false)).toEqual({ appearance: 'light', paletteId: 'canopy' });
    expect(resolveTheme(preferences, true)).toEqual({ appearance: 'dark', paletteId: 'current' });
    expect(resolveTheme({ ...preferences, mode: 'light' }, true)).toEqual({
      appearance: 'light',
      paletteId: 'canopy',
    });
  });
  it('migrates previous palette names without changing the selected colors', () => {
    expect(parseThemePreferences('{"light":"t3-chat","dark":"voicestudio-classic"}')).toEqual({
      mode: 'dark',
      light: 'signal',
      dark: 'heritage',
    });
  });
  it('keeps Studio as a valid saved palette for both appearances', () => {
    const preferences = parseThemePreferences('{"mode":"dark","light":"default","dark":"default"}');
    expect(preferences).toEqual({ mode: 'dark', light: 'default', dark: 'default' });
    expect(resolveTheme(preferences, false)).toEqual({ appearance: 'dark', paletteId: 'default' });
  });
  it('ships complete semantic colors for both appearances of every palette', () => {
    expect(BUILT_IN_THEMES.map((theme) => theme.id)).toEqual([
      'signal',
      'canopy',
      'current',
      'hearth',
      'orchid',
    ]);
    for (const theme of AVAILABLE_PALETTES)
      for (const mode of ['light', 'dark'] as const) {
        const colors = paletteColors(theme.id, mode);
        for (const role of THEME_COLOR_ROLES)
          expect(colors?.[role], `${theme.id}/${mode}/${role}`).toEqual(expect.any(String));
      }
    expect(paletteColors('heritage', 'dark')?.canvas).toBe('#211c29');
    expect(paletteColors('heritage', 'dark')?.messageAction).toBe('#d3869b');
    expect(parseThemePreferences('{"dark":"heritage"}').dark).toBe('heritage');
    expect(paletteColors('default', 'dark')).toBeNull();
    expect(colorVariable('sidebarRowHover')).toBe('--app-theme-sidebar-row-hover');
    expect(colorVariable('terminalSelection')).toBe('--app-theme-terminal-selection-background');
  });
  it("keeps Hearth's primary action colour clear of its destructive red", () => {
    const hue = (color: string | undefined) =>
      Number(/oklch\([\d.]+ [\d.]+ ([\d.]+)\)/.exec(color ?? '')?.[1]);
    for (const appearance of ['light', 'dark'] as const) {
      const colors = paletteColors('hearth', appearance)!;
      const gap = Math.abs(hue(colors.messageAction) - hue(colors.error));
      expect(Math.min(gap, 360 - gap), appearance).toBeGreaterThan(60);
    }
  });
});
