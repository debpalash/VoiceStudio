/**
 * parseScript — turn pasted prose or a screenplay into attributed lines.
 *
 * Three formats are recognised:
 *   0. Tagged script: `[Alice] dialogue` / `[Bob] dialogue` (podcast/audiobook
 *      style, #487) — auto-detected and parsed by `parseTaggedScript`.
 *   1. Screenplay:  `NAME: dialogue`            → { speaker: NAME, text }
 *   2. Prose:       narration with "quoted" bits → narration goes to the
 *                   Narrator; each quote is attributed to a nearby dialogue
 *                   tag ("said the fox" / "the fox asked").
 *
 * Returns an ordered array of { speaker, text }. Pure + testable; the caller
 * maps speakers → cast members (autoCast). Speaker "Narrator" is the default.
 */

const TAG_VERBS =
  'said|asked|replied|answered|whispered|shouted|murmured|cried|added|continued|' +
  'muttered|exclaimed|called|yelled|laughed|sighed|began|growled|responded|declared';

/** Normalise a raw captured name → Title Case, drop a leading "the"/punctuation. */
export function normalizeSpeaker(raw) {
  let s = String(raw || '')
    .trim()
    .replace(/^the\s+/i, '')
    .replace(/[.,!?:;"'“”]+$/g, '')
    .trim();
  if (!s) return 'Narrator';
  return s
    .split(/\s+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ');
}

function attributionName(before, after) {
  const V = TAG_VERBS;
  const tests = [
    new RegExp(`^[\\s,]*(?:${V})\\s+(?:the\\s+)?([A-Za-z][A-Za-z'-]*)`, 'i'), // "," asked the fox
    new RegExp(`^[\\s,]*(?:the\\s+)?([A-Za-z][A-Za-z'-]*)\\s+(?:${V})`, 'i'), // "," the owl said
  ].map((re) => after.match(re));
  const before2 = [
    new RegExp(`(?:the\\s+)?([A-Za-z][A-Za-z'-]*)\\s+(?:${V})\\s*[,:]?\\s*$`, 'i'), // The fox asked,
    new RegExp(`(?:${V})\\s+(?:the\\s+)?([A-Za-z][A-Za-z'-]*)\\s*[,:]?\\s*$`, 'i'), // asked the fox,
  ].map((re) => before.match(re));
  const hit = [...tests, ...before2].find(Boolean);
  return hit ? hit[1] : null;
}

// ── Tagged-script format: `[Name] dialogue` (#487) ──────────────────────────
//
// Inline markers like [pause], [pause 500ms], [voice:ID], [fast], [spell] must
// NOT be mistaken for speaker tags. A speaker tag is a `[...]` at line start
// whose bracket content has no `:` (rules out [voice:…]) and whose first word
// isn't a reserved marker keyword. Everything the parser emits keeps the same
// { speaker, text } shape as parseScript, so autoCast works unchanged.
const _RESERVED_MARKERS = new Set([
  'pause',
  'voice',
  'fast',
  'slow',
  'spell',
  'laughter',
  'sigh',
  'breath',
  'whisper',
  'emphasis',
  'em',
  'break',
]);
const _SPEAKER_TAG = /^\s*\[([^\]:]+)\]\s*(.*)$/;

function _isSpeakerTag(name) {
  const trimmed = String(name || '').trim();
  if (!trimmed) return false;
  const first = trimmed.toLowerCase().replace(/^\//, '').split(/\s+/)[0];
  return !_RESERVED_MARKERS.has(first);
}

/** True when the text uses `[Name]` speaker tags (so parseScript routes here). */
export function hasSpeakerTags(text) {
  return String(text || '')
    .split(/\r?\n/)
    .some((line) => {
      const m = line.match(_SPEAKER_TAG);
      return !!(m && _isSpeakerTag(m[1]));
    });
}

/**
 * Parse a `[Name] …` tagged script into ordered { speaker, text } lines.
 * A speaker's block runs until the next `[Name]` tag (multi-line dialogue is
 * joined). Any prose before the first tag is attributed to the Narrator.
 */
export function parseTaggedScript(text) {
  const out = [];
  const src = String(text || '')
    .replace(/\r\n/g, '\n')
    .trim();
  if (!src) return out;

  let cur = { speaker: 'Narrator', parts: [] };
  const flush = () => {
    const t = cur.parts.join('\n').trim();
    if (t) out.push({ speaker: cur.speaker, text: t });
    cur = { speaker: cur.speaker, parts: [] };
  };

  for (const line of src.split('\n')) {
    const m = line.match(_SPEAKER_TAG);
    if (m && _isSpeakerTag(m[1])) {
      flush();
      cur.speaker = normalizeSpeaker(m[1]);
      if (m[2] && m[2].trim()) cur.parts.push(m[2].trim());
    } else if (line.trim()) {
      cur.parts.push(line.trim());
    }
  }
  flush();
  return out;
}

export function parseScript(text) {
  const out = [];
  const src = String(text || '')
    .replace(/\r\n/g, '\n')
    .trim();
  if (!src) return out;

  // Tagged scripts (`[Alice] …`) are unambiguous — handle them first so a
  // pasted podcast/audiobook script auto-casts without any format toggle (#487).
  if (hasSpeakerTags(src)) return parseTaggedScript(src);

  const paras = src
    .split(/\n\s*\n/)
    .flatMap((p) => p.split(/\n/))
    .map((s) => s.trim())
    .filter(Boolean);

  for (const para of paras) {
    // 1. Screenplay "NAME: dialogue" (avoid matching URLs like http://…)
    const sp = para.match(/^([A-Za-z][A-Za-z0-9 ._'-]{0,30}):\s+(.+)$/);
    if (sp && !/^https?$/i.test(sp[1].trim())) {
      out.push({ speaker: normalizeSpeaker(sp[1]), text: sp[2].trim() });
      continue;
    }

    // 2. Prose with quoted dialogue (straight + curly double quotes)
    const quoteRe = /["“„]([^"“”„]+)["”]/g;
    let last = 0;
    let m;
    let found = false;
    const segs = [];
    while ((m = quoteRe.exec(para)) !== null) {
      found = true;
      const before = para.slice(last, m.index);
      const quote = (m[1] || '').trim();
      const after = para.slice(quoteRe.lastIndex);
      if (before.trim()) segs.push({ speaker: 'Narrator', text: before.trim() });
      const name = attributionName(before, after);
      if (quote) segs.push({ speaker: name ? normalizeSpeaker(name) : 'Narrator', text: quote });
      last = quoteRe.lastIndex;
    }
    if (!found) {
      out.push({ speaker: 'Narrator', text: para });
      continue;
    }
    const tail = para.slice(last).trim();
    if (tail) segs.push({ speaker: 'Narrator', text: tail });
    out.push(...segs.filter((s) => s.text));
  }
  return out;
}
