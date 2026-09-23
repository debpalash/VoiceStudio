/**
 * Story import helpers — turn an uploaded file into plain text the editor can
 * auto-cast or split. Pure + testable; the component handles file reading.
 */

// SubRip has no escaping, so `2 < 3` or `a<b and c>d` is dialogue. Only exact
// tag shapes are markup: `<i>`, `<c.x>`, `<v Name>`, `<font ...>`, karaoke
// timestamps. Mirrors _SRT_MARKUP_RE in backend/services/srt_parser.py.
const CUE_MARKUP = new RegExp(
  [
    '</?(?:[biu]|c|ruby|rt)(?:\\.[^\\s.<>]+)*>',
    '<(?:v|lang)(?:\\.[^\\s.<>]+)*[ \\t][^<>\\n]*>',
    '</(?:v|lang)>',
    '<font[ \\t][^<>\\n]*>',
    '</?font>',
    '<(?:\\d+:)?\\d{2}:\\d{2}\\.\\d{3}>',
  ].join('|'),
  'gi',
);
// SubRip/ASS overrides (`{\an8}`, `{\i1}`); the body excludes `{` to stay linear.
const ASS_OVERRIDE = /\{\\[^{}\n]*\}/g;
// `<br>` is a rendered line break, so it separates words instead of vanishing.
const LINE_BREAK = /<br[ \t]*\/?>/gi;

/** `text` with each span `re` matches replaced by `sep`. */
function dropMatches(text, re, sep = '') {
  // Slices around each match instead of String#replace: this is TTS text,
  // never HTML, and a tag-pattern replace trips CodeQL's sanitizer queries.
  let out = '';
  let last = 0;
  for (const m of text.matchAll(re)) {
    out += text.slice(last, m.index) + sep;
    last = m.index + m[0].length;
  }
  return out + text.slice(last);
}

/** Caption markup is not speech: karaoke spans, italics, `<br>`, `{\an8}` alignment. */
function spokenCueText(text) {
  const spaced = dropMatches(dropMatches(String(text || ''), ASS_OVERRIDE), LINE_BREAK, ' ');
  return dropMatches(spaced, CUE_MARKUP)
    .replace(/[^\S\n]+/g, ' ')
    .trim();
}

/** Strip SRT indices + timestamps, returning one cue's text per line. */
export function parseSrt(content) {
  const blocks = String(content || '')
    .replace(/\r\n/g, '\n')
    .split(/\n\s*\n/);
  const out = [];
  // Cues seen so far, and the index the current cue carried (null if none).
  let cues = 0;
  let index = null;
  for (const b of blocks) {
    const lines = b
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);
    const kept = [];
    let pending = null;
    lines.forEach((l, i) => {
      // Use a plain substring check for the SRT time arrow — a `/-->/` regex
      // trips CodeQL's js/bad-tag-filter (it mistakes it for HTML-comment
      // filtering).
      if (l.includes('-->')) {
        cues += 1;
        index = pending;
        pending = null;
        return;
      }
      // Digits right before a timestamp are its cue index when they open the
      // block. Inside a compact block they are only if they are the number the
      // sequence expects next; digits right under a timestamp are that cue's
      // dialogue ("3", "1984"), since a cue needs text.
      if (/^\d+$/.test(l) && lines[i + 1]?.includes('-->')) {
        const expected = (index ?? cues) + 1;
        if (
          i === 0 ||
          (index !== null && !lines[i - 1].includes('-->') && Number(l) === expected)
        ) {
          pending = Number(l);
          return;
        }
      }
      kept.push(l);
    });
    const text = spokenCueText(kept.join(' '));
    if (text) out.push(text);
  }
  return out.join('\n');
}

/** Convert an imported file's raw content → plain text, by extension. */
export function importToText(filename, content) {
  const ext = String(filename || '')
    .toLowerCase()
    .split('.')
    .pop();
  if (ext === 'srt') return parseSrt(content);
  // .txt and anything else: use as-is.
  return String(content || '');
}
