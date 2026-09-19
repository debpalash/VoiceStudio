import { isChapterLine } from './storyExport';

// Sentence-aware splitter for the "Paste & auto-split" panel. Walks the text
// and breaks at the closest sentence boundary that keeps each chunk under
// `maxChars`. Falls back to whitespace, then to the hard cap.
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

// Split presets for the Paste & Split panel. A storybook read by one narrator
// wants far coarser lines than a screenplay: every line is an independent
// generation (prosody resets, a join in the render), so "one line per
// sentence" is what makes a book feel like a string of separate takes.
//   sentences  — the sentence-aware splitter above, capped at `maxChars`.
//   paragraphs — one line per paragraph (blank-line separated; falls back to
//                single newlines when the text has no blank lines).
//   chapters   — one line per chapter body, split only at `# ` headings; the
//                heading itself stays its own line so it renders as a chapter
//                marker. Paragraph breaks inside a body are kept (the renderer
//                turns them into paragraph gaps).
// A chapter heading is always its own line in every mode. "Heading" means what
// the renderer means (`isChapterLine`: a non-empty H1) — `## Scene` or a bare
// `#` is body text there, so it must stay body text here.
export const SPLIT_MODES = ['sentences', 'paragraphs', 'chapters'];
export const DEFAULT_SPLIT_MODE = 'paragraphs';
export const DEFAULT_SPLIT_MAX = { sentences: 180, paragraphs: 500, chapters: 500 };

function splitHeadings(text) {
  // → [{ heading: string|null, body: string }]
  const sections = [];
  let current = { heading: null, body: [] };
  for (const line of String(text || '').split(/\r\n|\r|\n/)) {
    if (isChapterLine(line)) {
      sections.push(current);
      current = { heading: line.trim(), body: [] };
    } else {
      current.body.push(line);
    }
  }
  sections.push(current);
  return sections.map((sec) => ({ heading: sec.heading, body: sec.body.join('\n').trim() }));
}

function splitParagraphs(body) {
  let parts = body.split(/\n[ \t]*\n+/);
  if (parts.length === 1 && body.includes('\n')) parts = body.split('\n');
  return parts.map((part) => part.trim()).filter(Boolean);
}

export function splitStoryText(text, mode = DEFAULT_SPLIT_MODE, maxChars) {
  const out = [];
  for (const { heading, body } of splitHeadings(text)) {
    if (heading) out.push(heading);
    if (!body) continue;
    if (mode === 'chapters') out.push(body);
    else if (mode === 'paragraphs') out.push(...splitParagraphs(body));
    else out.push(...splitIntoChunks(body, maxChars ?? DEFAULT_SPLIT_MAX.sentences));
  }
  return out;
}
