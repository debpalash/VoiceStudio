import { describe, it, expect } from 'vitest';
import { selectionPlayhead, loopWindow, MIN_LOOP_SEC } from '../utils/audioTrim.js';

// The preview playhead must live on the SAME [start,end] buffer timeline as the
// waveform and the exported slice — never a second media-element timeline that
// can drift for VBR / mis-reported-duration files (#1210).
describe('selectionPlayhead', () => {
  it('reports the elapsed offset inside a non-looping selection', () => {
    expect(selectionPlayhead(2, 6, 0, false)).toBe(2);
    expect(selectionPlayhead(2, 6, 1.5, false)).toBeCloseTo(3.5, 6);
  });

  it('clamps at the selection end when not looping', () => {
    expect(selectionPlayhead(2, 6, 10, false)).toBe(6);
  });

  it('wraps within the selection window when looping', () => {
    expect(selectionPlayhead(2, 6, 0, true)).toBe(2);
    expect(selectionPlayhead(2, 6, 5, true)).toBeCloseTo(3, 6); // 5 % 4 = 1 -> 2+1
    expect(selectionPlayhead(2, 6, 4, true)).toBeCloseTo(2, 6); // exact wrap
  });

  it('never divides by zero on a degenerate selection', () => {
    expect(Number.isFinite(selectionPlayhead(3, 3, 2, true))).toBe(true);
    expect(selectionPlayhead(3, 3, 2, false)).toBe(3);
  });
});

// The loop window fed to a BufferSource must never collapse: a zero-width or
// inverted range makes Web Audio ignore loopStart/loopEnd and loop the WHOLE
// buffer, which is the preview≠selection bug #1210. loopStart < loopEnd always.
describe('loopWindow', () => {
  it('passes a normal selection through unchanged', () => {
    const { loopStart, loopEnd, seg } = loopWindow(2, 5, 8);
    expect(loopStart).toBeCloseTo(2, 6);
    expect(loopEnd).toBeCloseTo(5, 6);
    expect(seg).toBeCloseTo(3, 6);
  });

  it('floors an empty selection (a plain canvas click leaves start === end)', () => {
    const { loopStart, loopEnd } = loopWindow(7, 7, 8);
    expect(loopEnd).toBeGreaterThan(loopStart); // <- would be equal without the floor
    expect(loopEnd - loopStart).toBeCloseTo(MIN_LOOP_SEC, 6);
  });

  it('floors an inverted selection instead of producing a negative window', () => {
    const { loopStart, loopEnd } = loopWindow(5, 2, 8);
    expect(loopEnd).toBeGreaterThan(loopStart);
    expect(loopEnd - loopStart).toBeCloseTo(MIN_LOOP_SEC, 6);
  });

  it('clamps the window into the buffer near the end', () => {
    const { loopStart, loopEnd } = loopWindow(7.999, 9, 8);
    expect(loopStart).toBeLessThanOrEqual(8);
    expect(loopEnd).toBeLessThanOrEqual(8);
    expect(loopEnd).toBeGreaterThan(loopStart);
  });
});
