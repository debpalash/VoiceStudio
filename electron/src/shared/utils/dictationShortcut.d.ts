export const DEFAULT_SHORTCUT: string;

export interface ParsedShortcut {
  accelerator: string;
  alt: boolean;
  ctrl: boolean;
  meta: boolean;
  shift: boolean;
  code: string;
  modifierCodes: Set<string>;
}

export function parseShortcut(accelerator?: string, platform?: string): ParsedShortcut | null;
export function eventMatchesShortcut(
  event: KeyboardEvent,
  accelerator: string,
  platform?: string,
): boolean;
export function isShortcutRelease(event: KeyboardEvent, shortcut: ParsedShortcut | null): boolean;
export function formatShortcut(accelerator: string, platform?: string): string;
