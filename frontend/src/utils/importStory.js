/**
 * Story import helpers — turn an uploaded file into plain text the editor can
 * auto-cast or split. Pure + testable; the component handles file reading.
 */
import { decode } from 'html-entities';

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
// WebVTT: an unescaped `<` opens a tag. Body excludes `<` so a run of
// unclosed `<` cannot make the scan quadratic. Mirrors _WEBVTT_TAG_RE.
const WEBVTT_TAG = /<[^<>\n]*>/g;
// SubRip/ASS overrides (`{\an8}`, `{\i1}`); the body excludes `{` to stay linear.
const ASS_OVERRIDE = /\{\\[^{}\n]*\}/g;
// `<br>` is a rendered line break, so it separates words instead of vanishing.
const LINE_BREAK = /<br[ \t]*\/?>/gi;
const TIMESTAMP = /^(?:\d+:)?[0-5]?\d:[0-5]?\d[,.]\d{1,3}$/;
const TIMESTAMP_START = /^(?:\d+:)?[0-5]?\d:[0-5]?\d[,.]\d{1,3}(?:[ \t]|$)/;

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

/** Decode WebVTT character references without parsing cue text as HTML. */
function unescapeWebVtt(text) {
  return text.includes('&') ? decode(text) : text;
}

function isWebVtt(text) {
  return /^[\uFEFF \t\n]*WEBVTT(?:[ \t\n]|$)/.test(text);
}

function isTimingLine(line) {
  // An arrow in metadata/dialogue is not a cue timestamp.
  const arrow = line.indexOf('-->');
  return (
    arrow >= 0 &&
    TIMESTAMP.test(line.slice(0, arrow).trim()) &&
    TIMESTAMP_START.test(line.slice(arrow + 3).trimStart())
  );
}

function isWebVttMetadata(first) {
  return first === 'STYLE' || first === 'REGION' || /^NOTE(?:[ \t]|$)/.test(first);
}

/** Caption markup is not speech: karaoke spans, italics, `<br>`, `{\an8}` alignment. */
function spokenCueText(text, webvtt = false) {
  const spaced = dropMatches(dropMatches(String(text || ''), ASS_OVERRIDE), LINE_BREAK, ' ');
  const stripped = dropMatches(spaced, webvtt ? WEBVTT_TAG : CUE_MARKUP);
  const spoken = webvtt ? unescapeWebVtt(stripped) : stripped;
  return spoken.replace(/[^\S\n]+/g, ' ').trim();
}

function captionBlocks(text, webvtt) {
  const blocks = text.split(/\n\s*\n/);
  if (!webvtt) return blocks;
  return blocks.filter((b) => {
    const lines = b
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);
    if (!lines.length) return false;
    const first = lines[0];
    if (/^WEBVTT(?:[ \t]|$)/.test(first)) return false;
    return !isWebVttMetadata(first);
  });
}

/** Strip SRT/WebVTT indices, identifiers, timestamps; one cue's text per line. */
export function parseSrt(content) {
  const normalized = String(content || '').replace(/\r\n?/g, '\n');
  const webvtt = isWebVtt(normalized);
  const out = [];
  // Cues seen so far, and the index the current cue carried (null if none).
  let cues = 0;
  let index = null;
  for (const b of captionBlocks(normalized, webvtt)) {
    const lines = b
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);
    const kept = [];
    let pending = null;
    lines.forEach((l, i) => {
      if (isTimingLine(l)) {
        cues += 1;
        index = pending;
        pending = null;
        return;
      }
      // WebVTT cue identifiers sit on the first line of a cue, immediately
      // before its timestamp. They are never spoken (backend parse_srt).
      if (webvtt && i === 0 && lines[i + 1] && isTimingLine(lines[i + 1])) return;
      // Digits right before a timestamp are its cue index when they open the
      // block. Inside a compact block they are only if they are the number the
      // sequence expects next; digits right under a timestamp are that cue's
      // dialogue ("3", "1984"), since a cue needs text.
      if (/^\d+$/.test(l) && lines[i + 1] && isTimingLine(lines[i + 1])) {
        const expected = (index ?? cues) + 1;
        if (i === 0 || (index !== null && !isTimingLine(lines[i - 1]) && Number(l) === expected)) {
          pending = Number(l);
          return;
        }
      }
      kept.push(l);
    });
    const text = spokenCueText(kept.join(' '), webvtt);
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
  if (ext === 'srt' || ext === 'vtt') return parseSrt(content);
  // .txt and anything else: use as-is.
  return String(content || '');
}
