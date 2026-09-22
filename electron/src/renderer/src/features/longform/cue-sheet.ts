import {
  buildCueSheet,
  cueSheetFilename,
  cuesFromChapters,
} from '../../../../../../frontend/src/utils/storyExport';
import type { AudiobookRenderChapter } from './longform-session';

/**
 * The chapters a cue sheet may describe: the ones that actually rendered.
 *
 * The backend appends to `chapters_meta` — the source of the m4b's embedded
 * FFMETADATA chapters — only after a chapter succeeds, and `continue`s past a
 * failure. Counting a failed chapter here would give it a start time and push
 * every later cue forward, so the .txt would disagree with both the audio and
 * the chapters embedded in it. This filter is the whole correctness invariant.
 */
export function renderedChapters(
  chapters: readonly AudiobookRenderChapter[] | null | undefined,
): AudiobookRenderChapter[] {
  return (chapters ?? []).filter((chapter) => chapter.status !== 'failed');
}

export interface CueSheet {
  /** `HH:MM:SS<TAB>Title` per chapter, newline-joined, no trailing newline. */
  body: string;
  /** Derived from the render output: `audiobook_ab12.m4b` -> `audiobook_ab12.txt`. */
  filename: string;
}

/**
 * The cue sheet for a finished render, or `null` when there is nothing to
 * write — no chapters rendered, or the render never produced an output file.
 *
 * Returning `null` rather than an empty string keeps the caller from offering a
 * download that would save a blank document.
 */
export function cueSheetFor(
  chapters: readonly AudiobookRenderChapter[] | null | undefined,
  output: string | null | undefined,
): CueSheet | null {
  const body = buildCueSheet(cuesFromChapters(renderedChapters(chapters)));
  if (!body) return null;
  return { body, filename: cueSheetFilename(output) };
}
