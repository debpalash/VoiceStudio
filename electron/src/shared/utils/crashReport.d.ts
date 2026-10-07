export function rootCauseLine(text: string): string;
export function nativeCrashExcerpt(text: string): string;
export function nativeFaultSummary(text: string): string;
export function describeFramePath(path: string): { path: string; library: boolean };
export function clampCrashTail(text: string, max?: number): string;
