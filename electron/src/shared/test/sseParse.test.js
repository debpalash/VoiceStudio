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

  it('handles an empty buffer', () => {
    expect(splitSSEBuffer('')).toEqual({ lines: [], rest: '' });
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
