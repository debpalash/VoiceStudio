// Minimal helpers for reading a POST Server-Sent-Events stream via
// fetch() + response.body.getReader() (EventSource is GET-only, so it can't
// be used for our POST /audiobook stream). Pure + unit-tested so the
// buffer/line handling — the easy thing to get subtly wrong — is verified.

/**
 * Split an accumulated decoded-text buffer into complete lines plus the
 * trailing (possibly incomplete) remainder. Feed the remainder back in with
 * the next chunk, together with the returned ``endsWithCR``.
 *
 * CR, LF and CRLF all end a line (SSE spec). A CR at the very end of a chunk
 * ends its line at once, so a CR-only stream's final event is not held back;
 * passing ``afterCR`` then drops the LF that may open the next chunk, so a
 * CRLF split across two chunks is still one line ending, not an extra blank
 * line that would dispatch an event early.
 *
 * @param {string} buffer
 * @param {boolean} [afterCR] the previous call's ``endsWithCR``
 * @returns {{ lines: string[], rest: string, endsWithCR: boolean }}
 */
export function splitSSEBuffer(buffer, afterCR = false) {
  let text = buffer || '';
  if (afterCR && text.startsWith('\n')) text = text.slice(1);
  const parts = text.split(/\r\n|\r|\n/);
  const rest = parts.pop() ?? ''; // last part has no line ending yet
  return { lines: parts, rest, endsWithCR: text ? text.endsWith('\r') : afterCR };
}

/**
 * Parse one SSE line into its JSON event object, or null when the line isn't
 * a (well-formed) ``data:`` line. Tolerates ``data:`` with or without the
 * conventional trailing space, blank lines, and malformed JSON (returns null
 * rather than throwing, so one bad frame can't kill the read loop).
 *
 * @param {string} line
 * @returns {object | null}
 */
export function parseSSELine(line) {
  if (typeof line !== 'string' || !line.startsWith('data:')) return null;
  const payload = line.slice(5).trim(); // after 'data:'
  if (!payload) return null;
  try {
    return JSON.parse(payload);
  } catch {
    return null;
  }
}
