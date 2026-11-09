import { useSyncExternalStore } from 'react';
import { onStorageKey } from '@/lib/storage-events';

type Appearance = { font: 'inter' | 'system'; scale: number; glass: boolean };
export const appearanceScales = [90, 100, 110, 125] as const;
const key = 'voicestudio.appearance';
const listeners = new Set<() => void>();
export function parseAppearance(raw: string | null): Appearance {
  try {
    const value = JSON.parse(raw ?? '{}');
    return {
      font: value?.font === 'system' ? 'system' : 'inter',
      glass: value?.glass !== false,
      scale: appearanceScales.includes(value?.scale) ? value.scale : 100,
    };
  } catch {
    return { font: 'inter', scale: 100, glass: true };
  }
}
let current: Appearance;
try {
  current = parseAppearance(localStorage.getItem(key));
} catch {
  current = parseAppearance(null);
}
function apply() {
  document.documentElement.dataset.glass = String(current.glass);
  document.documentElement.style.fontSize = `${current.scale}%`;
  document.documentElement.style.setProperty(
    '--font-sans',
    `${current.font === 'inter' ? '"Inter Variable", ' : ''}-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif`,
  );
}
apply();
onStorageKey(key, () => {
  try {
    current = parseAppearance(localStorage.getItem(key));
  } catch {
    return;
  }
  apply();
  listeners.forEach((listener) => listener());
});
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export function updateAppearance(patch: Partial<Appearance>) {
  current = parseAppearance(JSON.stringify({ ...current, ...patch }));
  apply();
  try {
    localStorage.setItem(key, JSON.stringify(current));
  } catch {
    /* Session preferences still work. */
  }
  listeners.forEach((listener) => listener());
}

export function stepAppearanceScale(direction: -1 | 0 | 1) {
  const index = appearanceScales.indexOf(current.scale as (typeof appearanceScales)[number]);
  const next =
    direction === 0
      ? 100
      : appearanceScales[Math.max(0, Math.min(appearanceScales.length - 1, index + direction))];
  updateAppearance({ scale: next });
}

export function useAppearance() {
  const appearance = useSyncExternalStore(subscribe, () => current);
  return {
    ...appearance,
    update: updateAppearance,
  };
}
