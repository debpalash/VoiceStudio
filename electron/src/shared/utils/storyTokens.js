/**
 * Story-track text tokenizer.
 *
 * A Stories track is plain text plus two kinds of inline markers:
 *
 *   [pause 0.5s]            — emit `seconds` of silence
 *   [voice:<profile-id>]    — switch the active voice to `<profile-id>`
 *   [voice:default]         — revert to the track's default voice
 *
 * Markers are flat, never nested; the active voice persists until the next
 * voice marker or the end of the track. `parseStoryText` walks the text and
 * emits a sequence of events the preview/export pipeline can consume:
 *
 *   { type: 'chunk', text, profileId }   — speak `text` in `profileId`
 *   { type: 'pause', seconds }           — insert silence
 *
 * Whitespace-only chunks between markers are dropped so they don't introduce
 * audible "uhs" from the TTS model.
 */
import { roundHalfToEven, PAUSE_DEFAULT_MS, PAUSE_MAX_MS } from './longformParser';

// Single pattern that matches either token; the alternation captures the
// payload in group 1 (pause number), group 2 (pause unit ms|s), group 3 (voice).
// Widened to the CANONICAL dialect (#27) so the highlight overlay agrees with
// the render plan: bare [pause], [pause Nms], [pause Ns], [pause N.Ns], and
// [voice:[^\]\[]*] (empty [voice:] → default; a nested `[` → no match).
// ReDoS-safe: the unit's `(?:\s*(ms|s))?` is zero-width without a unit, so no
// two `\s*` overlap on the same whitespace run (mirrors PAUSE_RE).
const TOKEN_RE = /\[(?:\s*pause(?:\s+(\d+(?:\.\d+)?)(?:\s*(ms|s))?)?\s*|voice:\s*([^\][]*))\]/gi;

// Resolve a parsed (number, unit) pause to clamped ms — mirrors text.py:_pause_ms
// (banker's rounding via the shared roundHalfToEven so highlight == render).
function _pauseMs(num, unit) {
  if (num == null) return PAUSE_DEFAULT_MS;
  const value = parseFloat(num);
  if (!Number.isFinite(value)) return PAUSE_DEFAULT_MS;
  const ms = unit && unit.toLowerCase() === 's' ? value * 1000 : value;
  return Math.max(0, Math.min(roundHalfToEven(ms), PAUSE_MAX_MS));
}

export function parseStoryText(text, defaultProfileId = null) {
  const out = [];
  if (!text) return out;
  let currentProfile = defaultProfileId;
  let cursor = 0;
  // Use a fresh regex per call so concurrent callers don't share lastIndex state.
  const re = new RegExp(TOKEN_RE.source, TOKEN_RE.flags);
  let match;
  while ((match = re.exec(text)) !== null) {
    if (match.index > cursor) {
      const chunk = text.slice(cursor, match.index).trim();
      if (chunk) out.push({ type: 'chunk', text: chunk, profileId: currentProfile });
    }
    if (match[3] != null) {
      // voice branch — group 3 is defined even when empty ([voice:] → default)
      const id = match[3].trim();
      currentProfile = id === 'default' || id === '' ? defaultProfileId : id;
    } else {
      // pause branch — bare or numbered. ms→seconds; a 0-duration pause is
      // consumed (not spoken) but shows no overlay (the >0 guard below).
      const seconds =
        _pauseMs(match[1] != null ? match[1] : null, match[2] != null ? match[2] : null) / 1000;
      if (Number.isFinite(seconds) && seconds > 0) {
        out.push({ type: 'pause', seconds });
      }
    }
    cursor = re.lastIndex;
    if (re.lastIndex === match.index) re.lastIndex++; // zero-width guard
  }
  if (cursor < text.length) {
    const tail = text.slice(cursor).trim();
    if (tail) out.push({ type: 'chunk', text: tail, profileId: currentProfile });
  }
  return out;
}

/** True if `text` contains any inline marker we special-case for preview.
 *  Widened (#27) to recognize bare [pause] / [pause Nms] so the highlight path
 *  and the render path agree on which lines carry markers. */
export function hasStoryMarkers(text) {
  return /\[\s*pause\b|\[voice:/i.test(text || '');
}

/**
 * Wrap a selection with an inline voice switch.
 * - With a non-empty selection: `before [voice:X]selected[voice:default] after`
 * - With a collapsed caret: inserts `[voice:X]` at the caret (caller-controlled).
 *
 * Returns the new text — the caller is responsible for state updates.
 */
export function applyInlineVoice(text, selectionStart, selectionEnd, voiceId) {
  const s = Math.max(0, Math.min(selectionStart ?? 0, text.length));
  const e = Math.max(s, Math.min(selectionEnd ?? s, text.length));
  const before = text.slice(0, s);
  const middle = text.slice(s, e);
  const after = text.slice(e);
  if (!middle) {
    return `${before}[voice:${voiceId}]${after}`;
  }
  return `${before}[voice:${voiceId}]${middle}[voice:default]${after}`;
}

/**
 * Insert `token` (e.g. `[pause 0.5s]` or `[laughter]`) at `caret`, padding with
 * spaces so it survives a re-split and never glues onto a word. With a null/
 * out-of-range caret it appends to the end. Pure — the caller updates state.
 */
export function insertToken(text, caret, token) {
  const t = String(text || '');
  if (caret == null || caret < 0 || caret > t.length) {
    const sep = t.length && !/\s$/.test(t) ? ' ' : '';
    return `${t}${sep}${token}`;
  }
  const before = t.slice(0, caret);
  const after = t.slice(caret);
  const left = before.length && !/\s$/.test(before) ? `${before} ` : before;
  const right = after.length && !/^\s/.test(after) ? ` ${after}` : after;
  return `${left}${token}${right}`;
}
