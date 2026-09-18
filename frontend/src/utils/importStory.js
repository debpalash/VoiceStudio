/**
 * Story import helpers — turn an uploaded file into plain text the editor can
 * auto-cast or split. Pure + testable; the component handles file reading.
 */

/** Strip SRT indices + timestamps, returning one cue's text per line. */
export function parseSrt(content) {
  const blocks = String(content || '')
    .replace(/\r\n/g, '\n')
    .split(/\n\s*\n/);
  const out = [];
  // Cues seen so far, and the index the current cue carried (null if none).
  let cues = 0;
  let index = null;
  for (const b of blocks) {
    const lines = b
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);
    const kept = [];
    let pending = null;
    lines.forEach((l, i) => {
      // Use a plain substring check for the SRT time arrow — a `/-->/` regex
      // trips CodeQL's js/bad-tag-filter (it mistakes it for HTML-comment
      // filtering).
      if (l.includes('-->')) {
        cues += 1;
        index = pending;
        pending = null;
        return;
      }
      // Digits right before a timestamp are its cue index when they open the
      // block. Inside a compact block they are only if they are the number the
      // sequence expects next; digits right under a timestamp are that cue's
      // dialogue ("3", "1984"), since a cue needs text.
      if (/^\d+$/.test(l) && lines[i + 1]?.includes('-->')) {
        const expected = (index ?? cues) + 1;
        if (i === 0 || (!lines[i - 1].includes('-->') && Number(l) === expected)) {
          pending = Number(l);
          return;
        }
      }
      kept.push(l);
    });
    const text = kept.join(' ').trim();
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
  if (ext === 'srt') return parseSrt(content);
  // .txt and anything else: use as-is.
  return String(content || '');
}
