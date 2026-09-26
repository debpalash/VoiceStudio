export function isSherpaModel(id) {
  return typeof id === 'string' && id.startsWith('sherpa-');
}

/** Classify the backend's explicit sherpa final-frame contract. */
export function classifySherpaFinal(message) {
  const text = typeof message?.text === 'string' ? message.text.trim() : '';
  if (message?.final_kind === 'summary') return text ? 'summary' : 'terminator';
  if (message?.final_kind === 'utterance') return text ? 'utterance' : 'ignore';
  return 'ignore';
}

/** Return the EOF-summary suffix that has not already been committed live. */
export function sherpaSummaryTail(summaryText, committed) {
  const summary = (summaryText || '').trim();
  const delivered = (committed || []).join(' ').trim();
  if (!delivered) return summary;
  if (summary === delivered) return '';
  const prefix = `${delivered} `;
  return summary.startsWith(prefix) ? summary.slice(prefix.length).trim() : '';
}

/** Combine delivery outcomes without ever hiding a clipboard-only fallback. */
export function aggregateDeliveryKind(current, next) {
  const priority = { noop: 0, pasted: 1, inserted: 2, copied: 3 };
  if (!current) return next || null;
  if (!next) return current;
  return (priority[next] || 0) > (priority[current] || 0) ? next : current;
}

/**
 * Compute the keystroke delta to turn `prevTyped` (what we've already typed into
 * the focused field for the in-flight utterance) into `nextText` (the recognizer's
 * latest revision of that same utterance). Pure + exported for unit testing.
 *
 * Streaming recognizers don't only append — they REVISE earlier words ("recognise"
 * → "recognize", "to" → "two"). So we find the longest common prefix, retract
 * everything after it with backspaces, then type the corrected suffix. The common
 * case (pure append) yields `backspaces: 0` and just the new tail.
 *
 *   computeTypeDelta('hello wor', 'hello world') → { backspaces: 0, text: 'ld' }
 *   computeTypeDelta('hello to', 'hello two')    → { backspaces: 1, text: 'wo' }
 *   computeTypeDelta('hello', 'hello')           → { backspaces: 0, text: '' }  (noop)
 *
 * Returns `{ backspaces, text }`; `noop` is true when both are empty.
 */
export function computeTypeDelta(prevTyped, nextText) {
  const prev = prevTyped || '';
  const next = nextText || '';
  // Longest common prefix (by UTF-16 code unit — enigo types code points but the
  // backspace count we send is per-character; spread to count code points so an
  // astral char like an emoji retracts/types as one unit on every platform).
  const prevChars = Array.from(prev);
  const nextChars = Array.from(next);
  let i = 0;
  const max = Math.min(prevChars.length, nextChars.length);
  while (i < max && prevChars[i] === nextChars[i]) i++;
  const backspaces = prevChars.length - i;
  const text = nextChars.slice(i).join('');
  return { backspaces, text, noop: backspaces === 0 && text === '' };
}

/**
 * Map a failed `simulate_paste`/`simulate_type` invoke into an actionable
 * `{ kind, message }`. The Rust command prefixes its Err strings with the
 * failing layer — "a11y:" (macOS Accessibility not granted; the pill offers
 * open_accessibility_settings), "clipboard:" (couldn't write/restore the user
 * clipboard), "preflight:" (input was rejected before any key could be emitted)
 * or "paste:" (the synthetic ⌘V/Ctrl+V itself failed). Pure + exported for
 * unit testing.
 */
export function parsePasteError(err) {
  const raw = typeof err === 'string' ? err : (err && err.message) || String(err ?? '');
  for (const kind of ['a11y', 'clipboard', 'paste', 'preflight']) {
    if (raw.startsWith(`${kind}:`)) return { kind, message: raw.slice(kind.length + 1).trim() };
  }
  return { kind: 'paste', message: raw };
}
