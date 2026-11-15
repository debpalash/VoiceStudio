import { isChapterLine } from '@shared/utils/storyExport';
import type { Line } from './longform-session';

/** Keep independent speaker/override choices editable rather than discarding one. */
export function canMergeStoryLines(first: Line, next: Line | undefined): boolean {
  return Boolean(
    next &&
    !isChapterLine(first.text) &&
    !isChapterLine(next.text) &&
    (first.character ?? null) === (next.character ?? null) &&
    (first.profileId ?? null) === (next.profileId ?? null) &&
    (first.speed ?? null) === (next.speed ?? null),
  );
}

export function mergeStoryLineWithNext(lines: Line[], id: string): Line[] | null {
  const index = lines.findIndex((line) => line.id === id);
  if (index < 0 || !canMergeStoryLines(lines[index], lines[index + 1])) return null;
  const first = lines[index];
  const next = lines[index + 1];
  // The canonical voice reset also resets emotion, SSML-lite and pause runs.
  // Isolate bracket-bearing text (including incomplete markup) at the old line
  // boundary. Basic prose deliberately becomes one continuous spoken span.
  const reset = first.text.includes('[') || next.text.includes('[') ? '[voice:]' : '';
  return [
    ...lines.slice(0, index),
    { ...first, text: `${first.text}\n${reset}${next.text}` },
    ...lines.slice(index + 2),
  ];
}
