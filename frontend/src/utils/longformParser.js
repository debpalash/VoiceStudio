/**
 * Canonical longform marker parser — JS twin of
 * backend/services/longform_parser.py (#27).
 *
 * Byte-for-byte mirror of the Python parser, verified by the shared golden
 * corpus tests/fixtures/longform_parser_cases.json (asserted in both
 * tests/test_longform_parser.py and frontend/src/test/longformParser.test.js).
 * Do not "improve" one side without the other — the corpus will fail.
 *
 * Grammar precedence (outer→inner):  # chapter → [voice:] → [pause] → SSML-lite.
 */
import { parseSsmlLite, spellOut } from './ssmlLite';

export const PAUSE_DEFAULT_MS = 350;
export const PAUSE_MAX_MS = 10000;

// H1 only (## … narrate as body); title starts with \S so `# ` (no title) is
// body. Moved-equivalent of audiobook.py _HEADING_RE. Global+multiline.
const HEADING_RE = /^[ \t]*#[ \t]+(\S.*)$/gm;
// [voice:NAME] — content excludes BOTH brackets (mirrors _VOICE_RE).
const VOICE_RE = /\[voice:([^\][]*)\]/g;
// Pause dialect mirroring omnivoice.utils.text._PAUSE_RE. JS has no atomic
// group; the unit's own `(?:\s*(ms|s))?` is zero-width when no unit follows, so
// the trailing `\s*` is the ONLY consumer of trailing whitespace — no two `\s*`
// overlap on the same run (ReDoS-safe, matches the Python atomic group exactly
// on the full dialect + NO-MATCH boundary set).
const PAUSE_RE = /\[\s*pause(?:\s+(\d+(?:\.\d+)?)(?:\s*(ms|s))?)?\s*\]/gi;

/** Round half-to-even (banker's rounding) — matches Python int(round(x)). */
export function roundHalfToEven(x) {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff < 0.5) return f;
  if (diff > 0.5) return f + 1;
  // exact .5 tie → round to the even neighbour
  return f % 2 === 0 ? f : f + 1;
}

function _pauseMs(num, unit) {
  if (num == null) return PAUSE_DEFAULT_MS;
  const value = parseFloat(num);
  if (!Number.isFinite(value)) return PAUSE_DEFAULT_MS;
  const ms = unit && unit.toLowerCase() === 's' ? value * 1000 : value;
  const msInt = roundHalfToEven(ms);
  return Math.max(0, Math.min(msInt, PAUSE_MAX_MS));
}

/** Mirror of text.py:parse_pause_markers → [[spanText, pauseMsAfter], …]. */
function parsePauseMarkers(text) {
  if (!text || text.indexOf('[') === -1) return [[text, 0]];
  const segments = [];
  let last = 0;
  let pendingText = '';
  const re = new RegExp(PAUSE_RE.source, PAUSE_RE.flags);
  let m;
  while ((m = re.exec(text)) !== null) {
    pendingText += text.slice(last, m.index);
    last = re.lastIndex;
    const pause = _pauseMs(m[1] != null ? m[1] : null, m[2] != null ? m[2] : null);
    if (pendingText === '' && segments.length) {
      const [prevText, prevPause] = segments[segments.length - 1];
      segments[segments.length - 1] = [prevText, Math.min(prevPause + pause, PAUSE_MAX_MS)];
    } else {
      segments.push([pendingText, pause]);
    }
    pendingText = '';
    if (re.lastIndex === m.index) re.lastIndex++; // guard against zero-width
  }
  const tail = pendingText + text.slice(last);
  if (tail || !segments.length) segments.push([tail, 0]);
  return segments;
}

/** Mirror of the voice-split in _parse_chapter_body → [[voiceId, runText], …]. */
function parseVoiceRuns(body, defaultVoice) {
  const runs = [];
  let curVoice = defaultVoice;
  let last = 0;
  const re = new RegExp(VOICE_RE.source, VOICE_RE.flags);
  let m;
  while ((m = re.exec(body)) !== null) {
    if (m.index > last) runs.push([curVoice, body.slice(last, m.index)]);
    const id = (m[1] || '').trim();
    curVoice = id || defaultVoice;
    last = re.lastIndex;
    if (re.lastIndex === m.index) re.lastIndex++;
  }
  runs.push([curVoice, body.slice(last)]);
  return runs;
}

// A blank line (paragraph break) — mirrors _BLANK_LINE_RE in longform_parser.py.
const BLANK_LINE_RE = /\n[ \t\r]*\n/;

/**
 * Voice→pause→SSML layering for ONE chapter body (no chapter split). The
 * storyToSpans adapter calls this per spoken track. Mirrors Python
 * _parse_chapter_body.
 */
export function parseChapterBody(body, { defaultVoice = null, defaultSpeed = null } = {}) {
  const spans = [];
  for (const [voice, runText] of parseVoiceRuns(body, defaultVoice)) {
    for (const [spanText, pauseMs] of parsePauseMarkers(runText)) {
      const t = (spanText || '').trim();
      if (!t && pauseMs === 0) continue;
      // [text, speed, paragraphBreakBefore]. The whitespace BETWEEN two kept
      // segments is tracked so a blank line sitting on a markup boundary still
      // ends the line instead of being swallowed (py parity).
      const rendered = [];
      let between = '';
      for (const seg of t ? parseSsmlLite(t) : []) {
        const raw = seg.text;
        const st = (seg.spell ? spellOut(raw) : raw).trim();
        if (!st) {
          between += raw;
          continue;
        }
        const sp = seg.speed != null ? seg.speed : defaultSpeed;
        const lead = raw.slice(0, raw.length - raw.trimStart().length);
        rendered.push([st, sp, BLANK_LINE_RE.test(between + lead)]);
        between = raw.slice(raw.trimEnd().length);
      }
      if (!rendered.length) {
        if (pauseMs > 0) {
          spans.push({ voice_id: voice, text: '', pause_ms_after: pauseMs, speed: null });
        }
        continue;
      }
      rendered.forEach(([st, sp], j) => {
        const span = {
          voice_id: voice,
          text: st,
          pause_ms_after: j === rendered.length - 1 ? pauseMs : 0,
          speed: sp,
        };
        // Inline markup split one run of text: say how this span joins the next —
        // straight on, or across a blank line. Key present only here (py parity).
        if (j < rendered.length - 1) span.join = rendered[j + 1][2] ? 'paragraph' : 'continue';
        spans.push(span);
      });
    }
  }
  return spans;
}

/** Mirror of parse_script_to_spans → [{ title, spans:[{voice_id,text,pause_ms_after,speed}] }]. */
export function parseScriptToSpans(text, { defaultVoice = null, defaultSpeed = null } = {}) {
  if (!text) return [];
  const norm = text.replace(/\r\n?/g, '\n');

  const matches = [];
  const re = new RegExp(HEADING_RE.source, HEADING_RE.flags);
  let m;
  while ((m = re.exec(norm)) !== null) {
    matches.push({ index: m.index, end: m.index + m[0].length, title: m[1] });
    if (re.lastIndex === m.index) re.lastIndex++;
  }

  const raw = [];
  if (!matches.length) {
    raw.push([null, norm]);
  } else {
    const intro = norm.slice(0, matches[0].index);
    if (intro.trim()) raw.push([null, intro]);
    for (let i = 0; i < matches.length; i++) {
      const end = i + 1 < matches.length ? matches[i + 1].index : norm.length;
      raw.push([matches[i].title.trim(), norm.slice(matches[i].end, end)]);
    }
  }

  const chapters = [];
  for (const [title, body] of raw) {
    const spans = parseChapterBody(body, { defaultVoice, defaultSpeed });
    if (!spans.length) continue;
    chapters.push({ title: title || `Chapter ${chapters.length + 1}`, spans });
  }
  return chapters;
}
