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

function resolve(stack) {
  let speed = null;
  let spell = false;
  let emphasis = false;
  for (const name of stack) {
    const spec = TAGS[name];
    if (spec.speed !== null) speed = spec.speed;
    if (spec.spell !== null) spell = !!spec.spell;
    if (spec.emphasis !== null) emphasis = !!spec.emphasis;
  }
  return { speed, spell, emphasis };
}

export function parseSsmlLite(text) {
  if (!text) return [];
  if (!text.includes('[')) return [{ text, speed: null, spell: false, emphasis: false }];

  const segments = [];
  const stack = [];
  let last = 0;

  const emit = (chunk) => {
    if (!chunk) return;
    const props = resolve(stack);
    const prev = segments[segments.length - 1];
    if (
      prev &&
      prev.speed === props.speed &&
      prev.spell === props.spell &&
      prev.emphasis === props.emphasis
    ) {
      prev.text += chunk;
      return;
    }
    segments.push({ text: chunk, ...props });
  };

  const re = new RegExp(TAG_RE.source, TAG_RE.flags);
  let m;
  while ((m = re.exec(text)) !== null) {
    emit(text.slice(last, m.index));
    last = m.index + m[0].length;
    const isClose = m[1] === '/';
    const name = m[2].toLowerCase();
    if (isClose) {
      for (let i = stack.length - 1; i >= 0; i--) {
        if (stack[i] === name) {
          stack.splice(i, 1);
          break;
        }
      }
    } else {
      stack.push(name);
    }
  }
  emit(text.slice(last));
  return segments;
}

/** Space out a run for [spell]: "USA" → "U S A". */
export function spellOut(word) {
  return (word || '').split(/\s+/).join('').split('').join(' ');
}
