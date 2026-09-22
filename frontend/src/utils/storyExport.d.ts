interface Track {
  text: string;
  character?: string;
  profileId?: string | null;
  speed?: number | null;
}
export interface Cue {
  time: number;
  title: string;
}
export interface RenderedChapter {
  title?: string;
  duration_s?: number | string;
}
export function isChapterLine(text: string): boolean;
export function chapterTitle(text: string): string;
export function formatTimecode(sec: number): string;
/** `HH:MM:SS<TAB>Title` per cue, newline-joined, no trailing newline. */
export function buildCueSheet(chapters: Cue[] | null | undefined): string;
/** Start times accumulated over the SUCCESSFUL chapters, in order. */
export function cuesFromChapters(chapters: RenderedChapter[] | null | undefined): Cue[];
/** `audiobook_ab12.m4b` -> `audiobook_ab12.txt`; unknown shapes -> `cuesheet.txt`. */
export function cueSheetFilename(output: string | null | undefined): string;
export function exportStoryAudio<T extends Track>(
  tracks: T[],
  resolve: (track: T) => { profileId: string | null; speed: number | null },
  fetchChunk: (text: string, profileId: string | null, speed: number | null) => Promise<Blob>,
  progress?: ((done: number, total: number) => void) | null,
): Promise<{ blob: Blob; durationSec: number; chapters: { time: number; title: string }[] }>;
export function exportStems<T extends Track>(
  tracks: T[],
  resolve: (track: T) => { profileId: string | null; speed: number | null },
  fetchChunk: (text: string, profileId: string | null, speed: number | null) => Promise<Blob>,
  progress?: ((done: number, total: number) => void) | null,
): Promise<{ character: string; blob: Blob }[]>;
