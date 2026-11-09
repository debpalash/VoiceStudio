interface Track {
  text: string;
  character?: string;
  profileId?: string | null;
  speed?: number | null;
}
export interface Cue {
  title: string;
  time: number;
}
export interface RenderedChapter {
  duration_ms?: number | string;
  duration_s?: number | string;
  title?: string;
}

export function isChapterLine(text: string): boolean;
export function chapterTitle(text: string): string;
export function formatTimecode(sec: number): string;
export function buildCueSheet(cues: Cue[] | null | undefined): string;
export function cueSheetFilename(output: string | null | undefined): string;
export function cuesFromChapters(
  chapters: readonly RenderedChapter[] | null | undefined,
  fallbackTitle?: (n: number) => string,
): Cue[];
export function exportStoryAudio<T extends Track>(
  tracks: T[],
  resolve: (track: T) => { profileId: string | null; speed: number | null },
  fetchChunk: (
    text: string,
    profileId: string | null,
    speed: number | null,
    emotion?: string,
  ) => Promise<Blob>,
  progress?: ((done: number, total: number) => void) | null,
): Promise<{ blob: Blob; durationSec: number; chapters: { time: number; title: string }[] }>;
export function exportStems<T extends Track>(
  tracks: T[],
  resolve: (track: T) => { profileId: string | null; speed: number | null },
  fetchChunk: (
    text: string,
    profileId: string | null,
    speed: number | null,
    emotion?: string,
  ) => Promise<Blob>,
  progress?: ((done: number, total: number) => void) | null,
): Promise<{ character: string; blob: Blob }[]>;
