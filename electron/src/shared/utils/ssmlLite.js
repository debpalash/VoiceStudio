/**
 * SSML-LITE (client port) of backend/services/ssml_lite.py.
 *
 * Parity is no longer kept by hand: the canonical longform grammar (#27) is
 * exercised end-to-end by the shared golden corpus in
 * tests/fixtures/longform_parser_cases.json, asserted byte-for-byte against
 * BOTH this port (via longformParser.js → storyToSpans) and the Python parser.
 * A drift between the two SSML impls fails one of those two suites.
 *
 * Splits one narration line into ordered prosody segments so the Stories Editor
 * compiles the same `[slow]/[fast]/[emphasis]/[spell]` markup the Audiobook
 * backend parser honours. Pure; no deps.
 *
 *   parseSsmlLite(text) -> [{ text, speed, spell, emphasis }, …]
 *
 * Plain text → one segment {speed:null, spell:false, emphasis:false}. Tags nest
 * (innermost wins per property); an unclosed tag runs to end-of-line; a stray
 * close is ignored; adjacent identical-prosody segments merge.
 */
export const SLOW_SPEED = 0.85;
export const FAST_SPEED = 1.15;
const EMPHASIS_SPEED = 0.92;

const TAGS = {
  slow: { speed: SLOW_SPEED, spell: null, emphasis: null },
  fast: { speed: FAST_SPEED, spell: null, emphasis: null },
  emphasis: { speed: EMPHASIS_SPEED, spell: null, emphasis: true },
  spell: { speed: null, spell: true, emphasis: null },
};

// Fixed-literal alternation, no quantifier overlap → linear-time (ReDoS-safe).
const TAG_RE = /\[(\/?)(slow|fast|emphasis|spell)\]/gi;

function resolve(openTags) {
  let latest = -1;
  let speed = null;
  for (const name of ['slow', 'fast', 'emphasis']) {
    const positions = openTags[name];
    if (positions.length && positions[positions.length - 1] > latest) {
      latest = positions[positions.length - 1];
      speed = TAGS[name].speed;
    }
  }
  return { speed, spell: !!openTags.spell.length, emphasis: !!openTags.emphasis.length };
}

export function parseSsmlLite(text) {
  if (!text) return [];
  if (!text.includes('[')) return [{ text, speed: null, spell: false, emphasis: false }];

  const segments = [];
  const openTags = Object.fromEntries(Object.keys(TAGS).map((name) => [name, []]));
  let position = 0;
  let last = 0;

  const emit = (chunk) => {
    if (!chunk) return;
    const props = resolve(openTags);
    const prev = segments[segments.length - 1];
    if (
      prev &&
      prev.speed === props.speed &&
      prev.spell === props.spell &&
      prev.emphasis === props.emphasis
    ) {
      prev.text.push(chunk);
      return;
    }
    segments.push({ text: [chunk], ...props });
  };

  const re = new RegExp(TAG_RE.source, TAG_RE.flags);
  let m;
  while ((m = re.exec(text)) !== null) {
    emit(text.slice(last, m.index));
    last = m.index + m[0].length;
    const isClose = m[1] === '/';
    const name = m[2].toLowerCase();
    if (isClose) {
      // Pop the nearest same-name open even when another tag is above it.
      openTags[name].pop();
    } else {
      openTags[name].push(position++);
    }
  }
  emit(text.slice(last));
  return segments.map((seg) => ({ ...seg, text: seg.text.join('') }));
}

// Grapheme clusters when the runtime has them (so ZWJ emoji, flags and
// combining marks stay whole); otherwise code points. Never UTF-16 code
// units, which tear surrogate pairs into lone surrogates.
const graphemes =
  typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function'
    ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    : null;

function splitCharacters(text) {
  return graphemes ? Array.from(graphemes.segment(text), (part) => part.segment) : Array.from(text);
}

/** Space out a run for [spell]: "USA" → "U S A". */
export function spellOut(word) {
  // Match Python str.split(): Unicode whitespace plus the four C0 separators.
  // ECMAScript \s instead drops BOMs and omits NEL and those separators.
  return (word || '').split(/[\p{White_Space}\u001C-\u001F]+/u).flatMap(splitCharacters).join(' ');
}
