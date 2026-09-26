export function splitIntoChunks(text: string, maxChars: number): string[];
export type SplitMode = 'sentences' | 'paragraphs' | 'chapters';
export const SPLIT_MODES: SplitMode[];
export const DEFAULT_SPLIT_MODE: SplitMode;
export const DEFAULT_SPLIT_MAX: Record<SplitMode, number>;
export function splitStoryText(text: string, mode?: SplitMode, maxChars?: number): string[];
