export interface AudiobookRenderChapter {
  title?: string;
  status: string;
  duration_s?: number;
}

export interface AudiobookLyricsWord {
  text: string;
  start: number;
  end: number;
  chapterIndex: number;
}

export interface AudiobookLyricsChapter {
  title: string;
  start: number;
  end: number;
  wordStart: number;
  wordCount: number;
}

export interface AudiobookLyricsTimeline {
  chapters: AudiobookLyricsChapter[];
  words: AudiobookLyricsWord[];
}

export function evenSplitWords(
  text: string,
  start: number,
  end: number,
): Array<Omit<AudiobookLyricsWord, 'chapterIndex'>>;

export function scriptChapters(script: string): Array<{
  title: string;
  tokens: string[];
}>;

export function buildLyricsTimeline(
  script: string,
  options?: {
    chapters?: AudiobookRenderChapter[] | null;
    duration?: number;
  },
): AudiobookLyricsTimeline;

export function activeWordIndex(words: AudiobookLyricsWord[], time: number): number;
