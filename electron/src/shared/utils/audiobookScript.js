/**
 * Audiobook script helpers (#1217) — pure, testable text→data functions for the
 * Cast panel, live stats bar, and pre-flight validation. No React, no i18n: the
 * UI supplies labels; these only compute.
 *
 * The bracket-token regexes here MIRROR the canonical grammar in
 * `longformParser.js` / `backend/services/longform_parser.py` (voice + pause +
 * SSML-lite). They exist for name extraction / stats / warnings only — the
 * golden-corpus parser stays the single grammar source of truth for rendering.
 */
import { TAGS } from './constants';

// [voice:NAME] — content excludes BOTH brackets (mirrors _VOICE_RE). Global.
const VOICE_RE = /\[voice:([^\][]*)\]/g;
// H1 chapter heading (mirrors _HEADING_RE): `# <non-space>…`, multiline.
const HEADING_RE = /^[ \t]*#[ \t]+(\S.*)$/gm;
// Any bracket token — used to strip markup before the word count and to
// enumerate tokens for validation. Non-greedy, no nested brackets.
const BRACKET_RE = /\[[^\][]*\]/g;

// Recognized (non-voice) bracket tokens, so validation can flag the rest.
const PAUSE_TOKEN_RE = /^\[\s*pause(?:\s+\d+(?:\.\d+)?(?:\s*(?:ms|s))?)?\s*\]$/i;
const SSML_TOKEN_RE = /^\[\/?(?:slow|fast|emphasis|spell)\]$/i;
// [emotion:TEXT] (#3422) — parsed and stripped, never read aloud. Mirrors the parser.
const EMOTION_TOKEN_RE = /^\[emotion:[^\][]*\]$/i;
const REACTION_TOKENS = new Set(TAGS.map((s) => s.toLowerCase()));

/** ~ words a listener hears per minute at an audiobook narration pace. */
export const AUDIOBOOK_WPM = 155;

/**
 * Distinct `[voice:NAME]` names present in the script, in first-seen order.
 * Empty `[voice:]` (reset-to-default) is skipped. Names are trimmed.
 */
export function parseCastNames(text) {
  if (!text) return [];
  const seen = new Set();
  const names = [];
  const re = new RegExp(VOICE_RE.source, VOICE_RE.flags);
  let m;
  while ((m = re.exec(text)) !== null) {
    const name = (m[1] || '').trim();
    if (re.lastIndex === m.index) re.lastIndex++; // zero-width guard
    if (!name || seen.has(name)) continue;
    seen.add(name);
    names.push(name);
  }
  return names;
}

/** Text with every bracket markup token removed (for a spoken-word count). */
function stripMarkup(text) {
  return (text || '').replace(BRACKET_RE, ' ');
}

function headingSpans(norm) {
  const heads = [];
  const hre = new RegExp(HEADING_RE.source, HEADING_RE.flags);
  let match;
  while ((match = hre.exec(norm)) !== null) {
    heads.push({
      index: match.index,
      end: match.index + match[0].length,
      title: (match[1] || '').trim(),
    });
    if (hre.lastIndex === match.index) hre.lastIndex++;
  }
  return heads;
}

function spokenWordCount(text) {
  return stripMarkup(text).split(/\s+/).filter(Boolean).length;
}

/**
 * `# ` H1 chapters for the script outline. Offsets are into a CRLF-normalized
 * manuscript so the editor can select the heading and scroll to it.
 */
export function parseChapterOutline(text) {
  const norm = (text || '').replace(/\r\n?/g, '\n');
  const heads = headingSpans(norm);
  // One forward scan for line numbers: rescanning each heading's prefix is
  // quadratic and stalls typing in large chaptered manuscripts.
  let line = 1;
  let scanned = 0;
  return heads.map((head, index) => {
    for (let at = norm.indexOf('\n', scanned); at !== -1 && at < head.index;) {
      line++;
      at = norm.indexOf('\n', at + 1);
    }
    scanned = head.index;
    const bodyEnd = index + 1 < heads.length ? heads[index + 1].index : norm.length;
    return {
      index,
      title: head.title,
      start: head.index,
      headingEnd: head.end,
      line,
      words: spokenWordCount(norm.slice(head.end, bodyEnd)),
    };
  });
}

/**
 * Heading to insert at `caret`, with a leading newline when not already at a
 * line start and a trailing one when text follows on the same line (after
 * `end`, the selection end), so that text stays out of the heading.
 */
export function chapterHeadingSnippet(text, caret, end = caret) {
  const value = text || '';
  const clamp = (n) => (Number.isFinite(n) ? Math.max(0, Math.min(n, value.length)) : value.length);
  const at = clamp(caret);
  const to = Math.max(at, clamp(end));
  const atLineStart = at === 0 || value[at - 1] === '\n';
  const atLineEnd = to === value.length || value[to] === '\n';
  return (atLineStart ? '' : '\n') + '# ' + (atLineEnd ? '' : '\n');
}

/**
 * Live script stats: chapters (`# ` H1 count, ≥1 so a title-less script still
 * reads as one chapter), spoken word count (whitespace split, markup stripped),
 * and an estimated runtime in seconds at {@link AUDIOBOOK_WPM}.
 */
export function scriptStats(text) {
  const norm = (text || '').replace(/\r\n?/g, '\n');
  const headings = norm.match(new RegExp(HEADING_RE.source, HEADING_RE.flags)) || [];
  // Spoken words only: drop `# heading` lines (titles aren't narrated as body,
  // mirroring the parser) AND bracket markup, then whitespace-split.
  const spoken = stripMarkup(norm.replace(new RegExp(HEADING_RE.source, HEADING_RE.flags), ' '));
  const words = spoken.split(/\s+/).filter(Boolean).length;
  const chapters = Math.max(1, headings.length);
  const runtimeSec = words > 0 ? (words / AUDIOBOOK_WPM) * 60 : 0;
  return { chapters, words, runtimeSec };
}

/** Format seconds as a clock string: `H:MM` (≥1h) or `M:SS` (<1h). Pure. */
export function formatRuntimeClock(sec) {
  const total = Math.max(0, Math.round(sec));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * Pre-flight warnings for a script (non-blocking hints). Returns an array of
 * `{ type, ... }`:
 *   - `unknown_voice`  `{ name }` — a `[voice:NAME]` neither mapped in the cast
 *                       nor an exact profile id → will use the default voice.
 *   - `empty_chapter`  `{ title }` — a `# heading` with no spoken body.
 *   - `unknown_tag`    `{ tag }` — a bracket token outside the known grammar
 *                       (voice/emotion/pause/SSML-lite/reactions) → read aloud literally.
 *   - `emotion_ignored` `{ name }` — the script uses `[emotion:]` but the active
 *                       engine (`name`) takes no emotion; reported once.
 *
 * @param {string} text
 * @param {object} opts
 * @param {Set<string>|string[]} [opts.mappedNames] cast names with a voice mapped
 * @param {Set<string>|string[]} [opts.profileIds]  known profile ids (exact match)
 * @param {string|null} [opts.emotionIgnoredBy]  name of the active engine when it
 *   takes no emotion (`expression_caps.emotion === 'none'`); null/absent = supported
 */
export function validateScript(
  text,
  { mappedNames = [], profileIds = [], emotionIgnoredBy = null } = {},
) {
  const warnings = [];
  const norm = (text || '').replace(/\r\n?/g, '\n');
  const mapped = mappedNames instanceof Set ? mappedNames : new Set(mappedNames);
  const ids = profileIds instanceof Set ? profileIds : new Set(profileIds);

  // 1. Unknown voices — a name with no cast mapping and no exact profile match.
  for (const name of parseCastNames(norm)) {
    if (!mapped.has(name) && !ids.has(name)) warnings.push({ type: 'unknown_voice', name });
  }

  // 2. Empty chapters — a `# heading` whose body has no spoken text. Split on
  //    headings (mirroring the parser) and check each body for any word.
  const heads = headingSpans(norm);
  for (let i = 0; i < heads.length; i++) {
    const bodyEnd = i + 1 < heads.length ? heads[i + 1].index : norm.length;
    const body = norm.slice(heads[i].end, bodyEnd);
    if (!stripMarkup(body).trim()) warnings.push({ type: 'empty_chapter', title: heads[i].title });
  }

  // 3. Unrecognized bracket tokens — anything outside the known grammar.
  const seenTags = new Set();
  let usesEmotion = false;
  const bre = new RegExp(BRACKET_RE.source, BRACKET_RE.flags);
  let bm;
  while ((bm = bre.exec(norm)) !== null) {
    const tok = bm[0];
    if (bre.lastIndex === bm.index) bre.lastIndex++;
    const lower = tok.toLowerCase();
    const emotion = EMOTION_TOKEN_RE.test(tok);
    usesEmotion ||= emotion;
    const known =
      emotion ||
      VOICE_RE.test(tok) ||
      PAUSE_TOKEN_RE.test(tok) ||
      SSML_TOKEN_RE.test(tok) ||
      REACTION_TOKENS.has(lower);
    VOICE_RE.lastIndex = 0; // test() advances a /g regex — reset it
    if (!known && !seenTags.has(lower)) {
      seenTags.add(lower);
      warnings.push({ type: 'unknown_tag', tag: tok });
    }
  }
  // 4. Emotion the active engine will skip (the tags are still never spoken).
  if (usesEmotion && emotionIgnoredBy)
    warnings.push({ type: 'emotion_ignored', name: emotionIgnoredBy });

  return warnings;
}
