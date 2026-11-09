import { buildCueSheet, cuesFromChapters, cueSheetFilename } from '@shared/utils/storyExport';
import type { AudiobookRenderChapter } from './longform-session';

export interface CueSheet {
  filename: string;
  body: string;
}

const DELIVERED = new Set(['cached', 'done']);

export function renderedChapters(
  chapters: AudiobookRenderChapter[] | null | undefined,
): AudiobookRenderChapter[] {
  if (!chapters?.length) return [];
  return chapters.filter(({ status }) => DELIVERED.has(status));
}

export function cueSheetFor(
  chapters: AudiobookRenderChapter[] | null | undefined,
  output: string | null | undefined,
  fallbackTitle?: (n: number) => string,
): CueSheet | null {
  if (!output) return null;
  const delivered = renderedChapters(chapters);
  const cues = cuesFromChapters(delivered, fallbackTitle);
  const body = buildCueSheet(cues);
  if (body.length === 0) return null;
  return { filename: cueSheetFilename(output), body };
}
