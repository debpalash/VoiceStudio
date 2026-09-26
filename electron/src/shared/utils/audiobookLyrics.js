/**
 * Synced-lyrics timing for the audiobook player — pure, testable text→cue
 * functions, no React and no network. The player highlights the word under
 * `audio.currentTime`, so everything here reduces to: which words exist per
 * chapter, and what `[start, end]` window each one owns inside the final file.
 *
 * Timing sources, in order (mirrors backend/services/karaoke_ass.py):
 *
 * 1. Per-chapter durations the render stream already emits (`chapter` SSE
 *    events carry `duration_s`) — no new backend work, no ASR pass. Words
 *    inside a chapter are even-split across its span, exactly like the
 *    karaoke burn-in's old-job fallback.
 * 2. When those durations are unavailable, the whole book's words are
 *    even-split over the audio element's own duration, chapter spans falling
 *    out proportionally by word count.
 *
 * Chapter/token parsing reuses the canonical JS grammar twin, including
 * `[spell]` expansion, so cue count and order match the backend render.
 */
import { parseScriptToSpans } from './longformParser';

const WS = /\s+/;

/**
 * Uniformly distribute a text's whitespace tokens over `[start, end]` —
 * a direct port of `karaoke_ass.even_split_words`. Returns
 * `[{ text, start, end }]`; empty for blank text.
 */
export function evenSplitWords(text, start, end) {
  const tokens = String(text || '')
    .trim()
    .split(WS)
    .filter(Boolean);
  if (!tokens.length) return [];
  const s = Number(start) || 0;
  const dur = Math.max(0, (Number(end) || 0) - s) / tokens.length;
  return tokens.map((tok, i) => ({ text: tok, start: s + i * dur, end: s + (i + 1) * dur }));
}

/**
 * Split a script into the chapters the backend parser would render, each with
 * its display tokens: `[{ title, tokens }]`. Control tokens (voice / pause /
 * SSML-lite) are stripped — they shape delivery, nobody hears them — while
 * reaction tags (`[laughs]`…) stay: the engine performs those, so they get a
 * highlight window like any word. Returns `[]` for a blank script.
 */
export function scriptChapters(script) {
  return parseScriptToSpans(String(script || '')).map(({ title, spans }) => ({
    title,
    tokens: spans.flatMap((span) => span.text.split(WS).filter(Boolean)),
  }));
}

/**
 * Build the full highlight timeline for a rendered book.
 *
 * @param {string} script     the script the render was created from
 * @param {object} opts
 * @param {Array}  [opts.chapters]  the tab's per-chapter stream state, aligned
 *   with the backend plan: `{ title, status, duration_s }` where status is
 *   done | cached | failed. Failed chapters are absent from the muxed audio,
 *   so they get no cue and no time.
 * @param {number} [opts.duration]  the audio element's total duration — the
 *   proportional fallback used when stream timings are absent or don't line
 *   up with the script (edited after the render, stopped mid-book, …).
 * @returns {{ chapters: Array<{title, start, end, wordStart, wordCount}>,
 *            words: Array<{text, start, end, chapterIndex}> }}
 */
export function buildLyricsTimeline(script, { chapters = null, duration = 0 } = {}) {
  const parsed = scriptChapters(script);
  const empty = { chapters: [], words: [] };
  if (!parsed.length) return empty;

  const timed =
    Array.isArray(chapters) &&
    chapters.length === parsed.length &&
    chapters.every((c) => c?.status === 'failed' || Number.isFinite(c?.duration_s));

  const outChapters = [];
  const words = [];
  if (timed) {
    let at = 0;
    for (let i = 0; i < parsed.length; i++) {
      if (chapters[i].status === 'failed') continue; // not in the audio
      const start = at;
      const end = at + Math.max(0, chapters[i].duration_s);
      pushChapter(outChapters, words, parsed[i], chapters[i].title, start, end);
      at = end;
    }
    return { chapters: outChapters, words };
  }

  const total = Number(duration) || 0;
  if (total <= 0) return empty;
  const totalTokens = parsed.reduce((n, c) => n + c.tokens.length, 0);
  if (!totalTokens) return empty;
  // Proportional even split: every word owns the same slice of the file, so
  // chapter spans fall out of their word counts. Wordless (pause-only)
  // chapters get a zero-width span — nothing to highlight there anyway.
  const per = total / totalTokens;
  let at = 0;
  for (const chapter of parsed) {
    const end = at + chapter.tokens.length * per;
    pushChapter(outChapters, words, chapter, '', at, end);
    at = end;
  }
  return { chapters: outChapters, words };
}

function pushChapter(outChapters, words, parsedChapter, streamTitle, start, end) {
  const wordStart = words.length;
  const split = evenSplitWords(parsedChapter.tokens.join(' '), start, end);
  const chapterIndex = outChapters.length;
  for (const w of split) words.push({ ...w, chapterIndex });
  outChapters.push({
    title: streamTitle || parsedChapter.title,
    start,
    end,
    wordStart,
    wordCount: split.length,
  });
}

/**
 * Index of the word under playback time `t`: the last word whose start is
 * ≤ `t` (binary search — cue lists run to tens of thousands of words), or -1
 * before the first word. Inside a pause the previous word stays lit, matching
 * the karaoke sweep's "gaps finish the previous word" behaviour.
 */
export function activeWordIndex(words, t) {
  if (!Array.isArray(words) || !words.length || !(t >= words[0].start)) return -1;
  let lo = 0;
  let hi = words.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (words[mid].start <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}
