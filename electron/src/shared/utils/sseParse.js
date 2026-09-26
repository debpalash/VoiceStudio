// Minimal helpers for reading a POST Server-Sent-Events stream via
// fetch() + response.body.getReader() (EventSource is GET-only, so it can't
// be used for our POST /audiobook stream). Pure + unit-tested so the
// buffer/line handling — the easy thing to get subtly wrong — is verified.

/**
 * Split an accumulated decoded-text buffer into complete lines plus the
 * trailing (possibly incomplete) remainder. Feed the remainder back in with
 * the next chunk.
 *
 * @param {string} buffer
 * @returns {{ lines: string[], rest: string }}
 */
export function splitSSEBuffer(buffer) {
  const parts = (buffer || '').split('\n');
  const rest = parts.pop() ?? ''; // last part has no trailing newline yet
  return { lines: parts, rest };
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
