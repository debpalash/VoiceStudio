import { useSyncExternalStore } from 'react';
import { THEME_COLOR_ROLES } from '@/lib/themes/t3-palettes';
import {
  colorVariable,
  paletteColors,
  parseThemePreferences,
  resolveTheme,
  THEME_STORAGE_KEY,
  type ThemeMode,
  type ThemePreferences,
} from '@/lib/themes/theme-preferences';
import { onStorageKey } from '@/lib/storage-events';
export type Theme = 'dark' | 'light';
const listeners = new Set<() => void>();
const media =
  typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;
function readStored() {
  try {
    return parseThemePreferences(
      localStorage.getItem(THEME_STORAGE_KEY),
      localStorage.getItem('voicestudio.theme'),
    );
  } catch {
    return parseThemePreferences(null);
  }
}
let preferences = readStored();
function snapshot() {
  const resolved = resolveTheme(preferences, media?.matches ?? false);
  return { ...preferences, theme: resolved.appearance, paletteId: resolved.paletteId };
}
let current = snapshot();
function apply() {
  const root = document.documentElement;
  root.classList.toggle('dark', current.theme === 'dark');
  root.style.colorScheme = current.theme;
  const colors = paletteColors(current.paletteId, current.theme);
  if (colors) {
    root.dataset.themeId = current.paletteId;
    for (const role of THEME_COLOR_ROLES) root.style.setProperty(colorVariable(role), colors[role]);
  } else {
    delete root.dataset.themeId;
    for (const role of THEME_COLOR_ROLES) root.style.removeProperty(colorVariable(role));
  }
}
function refresh() {
  current = snapshot();
  apply();
  listeners.forEach((listener) => listener());
}
apply();
media?.addEventListener('change', () => {
  if (preferences.mode === 'system') refresh();
});
onStorageKey(THEME_STORAGE_KEY, () => {
  preferences = readStored();
  refresh();
});
// localStorage.clear() in another window reports a null key.
window.addEventListener('storage', (event) => {
  if (event.key !== null) return;
  preferences = readStored();
  refresh();
});
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export function updateTheme(patch: Partial<ThemePreferences>) {
  preferences = parseThemePreferences(JSON.stringify({ ...preferences, ...patch }));
  try {
    localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    /* Session settings remain usable. */
  }
  refresh();
}
export function setTheme(mode: ThemeMode) {
  updateTheme({ mode });
}
export function useTheme() {
  return {
    ...useSyncExternalStore(
      subscribe,
      () => current,
      () => current,
    ),
    setTheme,
    updateTheme,
  };
}
