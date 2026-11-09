/**
 * Canonical longform marker parser — JS twin of
 * backend/services/longform_parser.py (#27).
 *
 * Byte-for-byte mirror of the Python parser, verified by the shared golden
 * corpus tests/fixtures/longform_parser_cases.json (asserted in both
 * tests/test_longform_parser.py and electron/src/shared/test/longformParser.test.js).
 * Do not "improve" one side without the other — the corpus will fail.
 *
 * Grammar precedence (outer→inner):  # chapter → [voice:] → [emotion:] → [pause] → SSML-lite.
 */
import { parseSsmlLite, spellOut } from './ssmlLite';

export const PAUSE_DEFAULT_MS = 350;
export const PAUSE_MAX_MS = 10000;

// H1 only (## … narrate as body); title starts with \S so `# ` (no title) is
// body. Moved-equivalent of audiobook.py _HEADING_RE. Global+multiline.
const HEADING_RE = /^[ \t]*#[ \t]+(\S.*)$/gm;
// [voice:NAME] — content excludes BOTH brackets (mirrors _VOICE_RE).
const VOICE_RE = /\[voice:([^\][]*)\]/g;
// [emotion:TEXT] — free-text delivery direction until the next [emotion:] or
// voice run (#3422); empty resets. Mirrors _EMOTION_RE.
const EMOTION_RE = /\[emotion:([^\][]*)\]/gi;
/** Longest emotion direction kept, in code points (mirrors EMOTION_MAX_CHARS). */
export const EMOTION_MAX_CHARS = 200;
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

/** Mirror of _emotion_value: whitespace runs collapsed, capped, null if empty. */
function emotionValue(raw) {
  const collapsed = raw.replace(/[ \t\r\n]+/g, ' ').trim();
  // Array.from counts code points like Python, so a cut never splits a surrogate pair.
  const value = Array.from(collapsed).slice(0, EMOTION_MAX_CHARS).join('').trim();
  return value || null;
}

/** Mirror of _split_emotions → [pieces, emotionAfter]. */
function splitEmotions(segments, emotion) {
  const out = [];
  for (const seg of segments) {
    const text = seg.text;
    let last = 0;
    const re = new RegExp(EMOTION_RE.source, EMOTION_RE.flags);
    let m;
    while ((m = re.exec(text)) !== null) {
      out.push({ ...seg, text: text.slice(last, m.index), emotion });
      emotion = emotionValue(m[1] || '');
      last = re.lastIndex;
      if (re.lastIndex === m.index) re.lastIndex++;
    }
    out.push({ ...seg, text: text.slice(last), emotion });
  }
  return [out, emotion];
}

// A blank line: two newlines with only spaces/tabs between (single class, linear time).
const BLANK_LINE_RE = /\n[ \t]*\n/;

const trailingSpace = (x) => x.slice(x.trimEnd().length);
const leadingSpace = (x) => x.slice(0, x.length - x.trimStart().length);

/**
 * Keep the segments that still have spoken text and say, for each kept one, how it meets
 * the next kept one: 'paragraph' when a blank line lies in the whitespace between them
 * (its own trailing space, any dropped blank segments, the next one's leading space),
 * otherwise 'continue'. The final kept segment has no join.
 */
function keptSegments(segments, defaultSpeed) {
  const kept = [];
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const spoken = (seg.spell ? spellOut(seg.text) : seg.text).trim();
    if (spoken) {
      kept.push({
        index: i,
        text: spoken,
        speed: seg.speed != null ? seg.speed : defaultSpeed,
        emotion: seg.emotion ?? null,
      });
    }
  }
  return kept.map((item, k) => {
    const next = kept[k + 1];
    if (!next) return { ...item, join: null };
    let gap = trailingSpace(segments[item.index].text);
    for (let m = item.index + 1; m < next.index; m++) gap += segments[m].text;
    gap += leadingSpace(segments[next.index].text);
    return { ...item, join: BLANK_LINE_RE.test(gap) ? 'paragraph' : 'continue' };
  });
}

/**
 * Voice→pause→SSML layering for ONE chapter body (no chapter split). The
 * storyToSpans adapter calls this per spoken track. Mirrors Python
 * _parse_chapter_body.
 */
export function parseChapterBody(body, { defaultVoice = null, defaultSpeed = null } = {}) {
  const spans = [];
  for (const [voice, runText] of parseVoiceRuns(body, defaultVoice)) {
    // Every voice run starts neutral (mirrors the Python reset).
    let emotion = null;
    for (const [spanText, pauseMs] of parsePauseMarkers(runText)) {
      const t = (spanText || '').trim();
      if (!t && pauseMs === 0) continue;
      let segments = t ? parseSsmlLite(t) : [];
      if (new RegExp(EMOTION_RE.source, 'i').test(t))
        [segments, emotion] = splitEmotions(segments, emotion);
      else if (emotion) segments = segments.map((seg) => ({ ...seg, emotion }));
      const rendered = keptSegments(segments, defaultSpeed);
      if (!rendered.length) {
        if (pauseMs > 0) {
          spans.push({ voice_id: voice, text: '', pause_ms_after: pauseMs, speed: null });
        }
        continue;
      }
      rendered.forEach(({ text, speed, join, emotion: spanEmotion }, j) => {
        const last = j === rendered.length - 1;
        spans.push({
          voice_id: voice,
          text,
          pause_ms_after: last ? pauseMs : 0,
          speed,
          ...(join ? { join } : {}),
          ...(spanEmotion ? { emotion: spanEmotion } : {}),
        });
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
