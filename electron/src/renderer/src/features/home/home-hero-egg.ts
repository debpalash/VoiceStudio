/**
 * Contributor easter egg: once someone opens three different contributor
 * profiles from Home, the hero offers to hide itself. Hiding lasts only for the
 * current app version, so every update brings the contributors back.
 */
const HIDDEN_KEY = 'voicestudio.home.heroHiddenFor';
const CLICKS_KEY = 'voicestudio.home.profileClicks';
export const PROFILE_CLICKS_TO_UNLOCK = 3;

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable (private web sessions); the hero just stays visible.
  }
}

export function heroHiddenFor(version: string): boolean {
  return Boolean(version) && read(HIDDEN_KEY) === version;
}

export function clickedProfiles(): string[] {
  try {
    const value: unknown = JSON.parse(read(CLICKS_KEY) ?? '[]');
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string')
      : [];
  } catch {
    return [];
  }
}

/** Remembers a distinct profile link and returns how many have been opened. */
export function recordProfileClick(href: string): number {
  const clicked = new Set(clickedProfiles());
  clicked.add(href);
  write(CLICKS_KEY, JSON.stringify([...clicked]));
  return clicked.size;
}

/** Hides the hero until the version changes; the next egg needs three new clicks. */
export function hideHeroFor(version: string) {
  write(HIDDEN_KEY, version);
  write(CLICKS_KEY, null);
}
