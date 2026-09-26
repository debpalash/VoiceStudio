export function parseCastNames(text: string): string[];
export function scriptStats(text: string): { chapters: number; words: number; runtimeSec: number };
export function formatRuntimeClock(seconds: number): string;
export function validateScript(
  text: string,
  options?: { mappedNames?: string[]; profileIds?: string[] },
): { type: string; name?: string; title?: string; tag?: string }[];
export const AUDIOBOOK_WPM: number;
