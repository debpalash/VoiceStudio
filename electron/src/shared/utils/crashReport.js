const MAX_CRASH_TAIL_CHARS = 1200;
const CHAIN_MARKER_RE =
  /^(?:The above exception was the direct cause of the following exception|During handling of the above exception, another exception occurred):?$/;

/** The error line that ends the FIRST block of a chained traceback — i.e. the
 *  original cause. Empty string when `text` is not a chained traceback. */
export function rootCauseLine(text) {
  const lines = text.split('\n');
  const marker = lines.findIndex((l) => CHAIN_MARKER_RE.test(l.trim()));
  if (marker <= 0) return '';
  // Walk back past the marker's blank line to the last non-indented line —
  // traceback frames are indented, the exception line is not.
  for (let i = marker - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (line.trim() && !/^\s/.test(line)) return line.trim();
  }
  return '';
}

// Below this much room for actual log output, the root-cause header stops being
// worth its cost — a labelled line with almost nothing under it is harder to
// act on than the raw newest output.
const MIN_TAIL_CHARS = 400;

/** Python's native-fault dump puts the failing frame FIRST, unlike a regular
 * traceback. Prefer its current thread over other threads and extension lists. */
export function nativeCrashExcerpt(text) {
  const lines = text.split('\n');
  const start = lines.findLastIndex((line) =>
    /^(?:Fatal Python error:|Windows fatal exception:)/.test(line.trim()),
  );
  if (start < 0) return '';
  const dump = lines.slice(start);
  const current = dump.findIndex((line) => /^Current thread\b/.test(line.trim()));
  const frames = current >= 0 ? dump.slice(current) : dump.slice(1);
  const end = frames.findIndex(
    (line, index) =>
      line.trim().startsWith('Extension modules:') ||
      (index > 0 && /^(?:Thread|Current thread)\b/.test(line.trim())),
  );
  return [dump[0], ...(end < 0 ? frames : frames.slice(0, end))].join('\n').trim();
}

/** Bound the crash stderr to `max` characters, keeping the newest end AND — for
 *  a chained traceback — the root cause that would otherwise be cut.
 *
 *  The result never exceeds `max`: the prefix is budgeted for BEFORE slicing,
 *  not added on top of a full-size tail. */
export function clampCrashTail(text, max = MAX_CRASH_TAIL_CHARS) {
  const native = nativeCrashExcerpt(text);
  if (native) {
    const suffix = '\n… (truncated)';
    return native.length <= max
      ? native
      : (native.slice(0, Math.max(0, max - suffix.length)) + suffix).slice(0, max);
  }
  if (text.length <= max) return text;

  const PLAIN = '… (truncated)';
  const plainTail = () => `${PLAIN}\n${text.slice(-Math.max(0, max - PLAIN.length - 1))}`;

  const root = rootCauseLine(text);
  if (!root) return plainTail();

  const prefix = `${root}\n… (truncated — chained traceback; the line above is the original cause)\n`;
  const room = max - prefix.length;
  if (room < MIN_TAIL_CHARS) return plainTail();

  const kept = text.slice(-room);
  // Already visible in what we keep — repeating it is noise, and the budget is
  // better spent on more log.
  if (kept.includes(root)) return plainTail();
  return prefix + kept;
}
