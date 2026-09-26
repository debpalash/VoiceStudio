export type PasteTranslationMode = 'timestamped' | 'numbered' | 'plain';

export interface PasteTranslationCue {
  start: number;
  end: number;
  text: string;
}

export interface PasteTranslationSegment {
  id: string;
  start: number;
  end: number;
  text: string;
}

export interface PasteTranslationRow {
  id: string;
  index: number;
  start: number;
  end: number;
  before: string;
  after: string | null;
  matched: boolean;
}

export interface PasteTranslationPlan {
  mode: PasteTranslationMode;
  rows: PasteTranslationRow[];
  matchedCount: number;
  unmatchedCount: number;
  sourceCount: number;
  unusedCount: number;
}

export function detectPasteMode(text: string): PasteTranslationMode;

export function buildPastePlan(
  text: string,
  segments: PasteTranslationSegment[],
  options?: { mode?: PasteTranslationMode; cues?: PasteTranslationCue[] },
): PasteTranslationPlan;
