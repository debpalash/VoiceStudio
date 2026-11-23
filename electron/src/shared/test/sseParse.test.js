import { describe, it, expect } from 'vitest';
import { splitSSEBuffer, parseSSELine } from '../utils/sseParse';

describe('splitSSEBuffer', () => {
  it('returns complete lines and keeps the trailing partial as rest', () => {
    const { lines, rest } = splitSSEBuffer('a\nb\nc-partial');
    expect(lines).toEqual(['a', 'b']);
    expect(rest).toBe('c-partial');
  });

  it('a trailing newline yields an empty rest', () => {
    const { lines, rest } = splitSSEBuffer('x\ny\n');
    expect(lines).toEqual(['x', 'y']);
    expect(rest).toBe('');
  });

  it('splits mixed SSE line endings and ends a chunk-final CR at once', () => {
    const first = splitSSEBuffer('a\rb\r\nc\nd\r');
    expect(first).toEqual({ lines: ['a', 'b', 'c', 'd'], rest: '', endsWithCR: true });
    // The LF opening the next chunk completes that CRLF: no extra blank line.
    expect(splitSSEBuffer(first.rest + '\ne\r\r', first.endsWithCR)).toEqual({
      lines: ['e', ''],
      rest: '',
      endsWithCR: true,
    });
    // Without a preceding CR, a leading LF is a real (blank) line ending.
    expect(splitSSEBuffer('\nx\n').lines).toEqual(['', 'x']);
  });

  it('keeps a pending CR across an empty chunk', () => {
    expect(splitSSEBuffer('', true).endsWithCR).toBe(true);
    expect(splitSSEBuffer('\ny\n', true).lines).toEqual(['y']);
  });

  it('clears the pending CR once a lone LF chunk completes it', () => {
    const lf = splitSSEBuffer('\n', true);
    expect(lf).toEqual({ lines: [], rest: '', endsWithCR: false });
    // The next LF is a real blank line again: it dispatches the event.
    expect(splitSSEBuffer('\ndata: x\n\n', lf.endsWithCR).lines).toEqual(['', 'data: x', '']);
  });

  it('handles an empty buffer', () => {
    expect(splitSSEBuffer('')).toEqual({ lines: [], rest: '', endsWithCR: false });
  });
});

describe('parseSSELine', () => {
  it('parses a data line (with the conventional space)', () => {
    expect(parseSSELine('data: {"type":"chapter","index":0}')).toEqual({
      type: 'chapter',
      index: 0,
    });
  });

  it('parses a data line without the space', () => {
    expect(parseSSELine('data:{"type":"done"}')).toEqual({ type: 'done' });
  });

  it('ignores non-data lines and blanks', () => {
    expect(parseSSELine('')).toBeNull();
    expect(parseSSELine(':keepalive')).toBeNull();
    expect(parseSSELine('event: ping')).toBeNull();
    expect(parseSSELine('data: ')).toBeNull();
  });

  it('returns null on malformed JSON rather than throwing', () => {
    expect(parseSSELine('data: {not json')).toBeNull();
  });
});
