// Sentence-aware splitter for the "Paste & auto-split" panel. Walks the text
// and breaks at the closest sentence boundary that keeps each chunk under
// `maxChars`. Falls back to whitespace, then to the hard cap.
import { isChapterLine } from './storyExport';

export function splitIntoChunks(text, maxChars) {
  const out = [];
  const clean = String(text || '')
    .replace(/\r\n/g, '\n')
    .trim();
  if (!clean) return out;
  const max = Math.max(40, Math.min(2000, maxChars | 0));
  let i = 0;
  while (i < clean.length) {
    const remain = clean.length - i;
    if (remain <= max) {
      out.push(clean.slice(i).trim());
      break;
    }
    const window = clean.slice(i, i + max);
    let cut = -1;
    for (let j = window.length - 1; j > Math.floor(max * 0.4); j--) {
      if (/[.!?\u3002\uFF01\uFF1F]/.test(window[j])) {
        cut = j + 1;
        break;
      }
    }
    if (cut < 0) {
      for (let j = window.length - 1; j > Math.floor(max * 0.4); j--) {
        if (/\s/.test(window[j])) {
          cut = j;
          break;
        }
      }
    }
    if (cut < 0) cut = max;
    out.push(clean.slice(i, i + cut).trim());
    i += cut;
  }
  return out.filter(Boolean);
}

export const SPLIT_MODES = ['sentences', 'paragraphs', 'chapters'];
export const DEFAULT_SPLIT_MODE = 'paragraphs';
// Character cap per produced line, by mode.
export const DEFAULT_SPLIT_MAX = { sentences: 180, paragraphs: 500, chapters: 500 };

// Trim each piece and keep only the ones with something left.
function keepFilled(pieces) {
  return pieces.map((piece) => piece.trim()).filter((piece) => piece.length > 0);
}

// Walk the lines once, starting a fresh block at every chapter heading.
// Each block is { heading, body } where body is the trimmed text under it.
function toBlocks(text) {
  const lines = String(text || '').split(/\r\n|\r|\n/);
  const blocks = [];
  let open = { heading: null, rows: [] };
  for (const row of lines) {
    if (!isChapterLine(row)) {
      open.rows.push(row);
      continue;
    }
    blocks.push(open);
    open = { heading: row.trim(), rows: [] };
  }
  blocks.push(open);
  return blocks.map(({ heading, rows }) => ({ heading, body: rows.join('\n').trim() }));
}

// Blank-line paragraphs; a lone block with hard line breaks degrades to one entry per line.
function byParagraph(body) {
  const paras = keepFilled(body.split(/\n[ \t]*\n+/));
  return paras.length === 1 && body.indexOf('\n') >= 0 ? keepFilled(body.split('\n')) : paras;
}

const BODY_SPLITTERS = {
  chapters: (body) => [body],
  paragraphs: byParagraph,
};

/**
 * Turn pasted text into script lines for a preset: 'sentences' (size-capped),
 * 'paragraphs' (the default) or 'chapters' (a whole chapter body per entry).
 * A chapter heading always becomes a line of its own.
 */
export function splitStoryText(text, mode = DEFAULT_SPLIT_MODE, maxChars) {
  const splitBody =
    BODY_SPLITTERS[mode] ??
    ((body) => splitIntoChunks(body, maxChars ?? DEFAULT_SPLIT_MAX.sentences));
  return toBlocks(text).flatMap(({ heading, body }) => [
    ...(heading ? [heading] : []),
    ...(body ? splitBody(body) : []),
  ]);
}
