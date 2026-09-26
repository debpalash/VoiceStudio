import { describe, it, expect } from 'vitest';
import { consumeLongformStream } from '../utils/longformStream';

// Build a fake fetch Response whose body streams the given chunks of text.
function streamResponse(chunks) {
  const enc = new TextEncoder();
  let i = 0;
  return {
    body: {
      getReader() {
        return {
          read() {
            if (i < chunks.length)
              return Promise.resolve({ done: false, value: enc.encode(chunks[i++]) });
            return Promise.resolve({ done: true, value: undefined });
          },
        };
      },
    },
  };
}

function sse(obj) {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

describe('consumeLongformStream', () => {
  it('parses every event across chunk boundaries, in order', async () => {
    // Split one event across two reads to exercise the buffer.
    const full =
      sse({ type: 'started', chapters: 2 }) +
      sse({ type: 'chapter', index: 0, total: 2, title: 'One' }) +
      sse({ type: 'chapter', index: 1, total: 2, title: 'Two' }) +
      sse({ type: 'done', output: 'book.m4b' });
    const mid = Math.floor(full.length / 2);
    const res = streamResponse([full.slice(0, mid), full.slice(mid)]);

    const events = [];
    await consumeLongformStream(res, (e) => events.push(e));

    expect(events.map((e) => e.type)).toEqual(['started', 'chapter', 'chapter', 'done']);
    expect(events[0].chapters).toBe(2);
    expect(events.at(-1).output).toBe('book.m4b');
  });

  it('stops early when isAborted() returns true', async () => {
    const res = streamResponse([
      sse({ type: 'started', chapters: 5 }),
      sse({ type: 'chapter', index: 0 }),
    ]);
    const events = [];
    let aborted = false;
    await consumeLongformStream(
      res,
      (e) => {
        events.push(e);
        aborted = true;
      },
      { isAborted: () => aborted },
    );
    // First read delivers the 'started' event, then isAborted() trips before the next read.
    expect(events.length).toBe(1);
  });

  it('throws when the response has no body', async () => {
    await expect(consumeLongformStream({}, () => {})).rejects.toThrow(/no response stream/);
  });

  // #1216 — a real Stop must release the stream, not just break the read loop:
  // reader.cancel() closes the fetch so the backend sees the disconnect.
  it('cancels the reader when an AbortSignal is already aborted', async () => {
    let cancelled = false;
    const res = {
      body: {
        getReader: () => ({
          read: () => Promise.resolve({ done: true, value: undefined }),
          cancel: () => {
            cancelled = true;
            return Promise.resolve();
          },
        }),
      },
    };
    const ctrl = new AbortController();
    ctrl.abort();
    const events = [];
    await consumeLongformStream(res, (e) => events.push(e), { signal: ctrl.signal });
    expect(cancelled).toBe(true);
    expect(events).toEqual([]); // never read a byte
  });

  it('swallows an abort-rejected read and cancels the reader when the signal fires mid-read', async () => {
    const ctrl = new AbortController();
    let cancelled = false;
    let n = 0;
    const res = {
      body: {
        getReader: () => ({
          read: () => {
            n += 1;
            if (n === 1)
              return Promise.resolve({
                done: false,
                value: new TextEncoder().encode(sse({ type: 'started', chapters: 3 })),
              });
            // 2nd read stays pending until the signal aborts, then rejects like
            // a real fetch body does on AbortController.abort().
            return new Promise((_, reject) => {
              ctrl.signal.addEventListener('abort', () =>
                reject(new DOMException('Aborted', 'AbortError')),
              );
            });
          },
          cancel: () => {
            cancelled = true;
            return Promise.resolve();
          },
        }),
      },
    };
    const events = [];
    const p = consumeLongformStream(res, (e) => events.push(e), { signal: ctrl.signal });
    // Let the first read + 'started' event flush, then Stop.
    await Promise.resolve();
    ctrl.abort();
    await expect(p).resolves.toBeUndefined(); // abort is NOT an error
    expect(events.map((e) => e.type)).toEqual(['started']);
    expect(cancelled).toBe(true);
  });
});
