/**
 * Shared consumer for the longform render SSE stream (Stories + Audiobook).
 *
 * Both editors compile to a chapter/span plan and stream progress from the same
 * server-side renderer (`_render_longform_sse`), emitting the same event shapes:
 *   { type: 'started', chapters }
 *   { type: 'chapter' | 'chapter_error', index, total, title }
 *   { type: 'assembling' }
 *   { type: 'done', output, cached_chapters?, failed_chapters? }
 *   { type: 'error', error }
 *
 * This factors out the identical read/decode/split/parse loop that lived in
 * both StoriesEditor and AudiobookTab — so a protocol change lives in one place
 * and each editor only supplies its own per-event state handling.
 */
import { splitSSEBuffer, parseSSELine } from './sseParse';

/**
 * Read `res.body` as an SSE stream and invoke `onEvent(evt)` for every parsed
 * event. Returns when the stream ends, `isAborted()` becomes true, or `signal`
 * fires.
 *
 * Stopping a longform render is a two-part contract (#1216): breaking the read
 * loop alone leaves the fetch open, so the SERVER keeps rendering the whole book
 * into a stream nobody reads. On abort we therefore `reader.cancel()` to release
 * the body stream and close the connection — that disconnect is what the
 * backend's `is_disconnected()` poll sees, so it stops scheduling chapters.
 * Callers should ALSO abort the fetch's `AbortController` (passed as `signal`)
 * so a stop that lands before the first byte is honoured too.
 *
 * @param {Response} res        fetch Response whose body is the SSE stream
 * @param {(evt: object) => void} onEvent  called once per parsed event
 * @param {{ isAborted?: () => boolean, signal?: AbortSignal }} [opts]
 */
export async function consumeLongformStream(res, onEvent, { isAborted, signal } = {}) {
  if (!res || !res.body) throw new Error('no response stream');
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const aborted = () => Boolean((isAborted && isAborted()) || (signal && signal.aborted));
  // Releasing the reader cancels the underlying stream (closes the fetch), so
  // the server sees the disconnect. Best-effort: a stream already closed/errored
  // — or a caller's fake reader without cancel() — must not throw here.
  const releaseStream = async () => {
    try {
      await reader.cancel();
    } catch {
      /* already closed/errored, or no cancel() — nothing to release */
    }
  };

  try {
    while (true) {
      if (aborted()) {
        await releaseStream();
        return;
      }
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { lines, rest } = splitSSEBuffer(buffer);
      buffer = rest;
      for (const line of lines) {
        const evt = parseSSELine(line);
        if (evt) onEvent(evt);
      }
    }
  } catch (e) {
    // An abort mid-read (AbortController.abort() / reader.cancel()) rejects the
    // pending read() — swallow it when WE initiated the stop; re-throw a genuine
    // stream/transport error so callers still surface it.
    if (aborted()) {
      await releaseStream();
      return;
    }
    throw e;
  }
}
