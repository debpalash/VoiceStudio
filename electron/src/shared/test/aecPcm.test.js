import { describe, it, expect } from 'vitest';
import { AEC_NEAR, AEC_FAR, floatToInt16, tagFrame, frameFromFloat } from '../utils/aec/pcm';

describe('aec/pcm helpers', () => {
  it('converts Float32 [-1,1] to clamped Int16', () => {
    const out = floatToInt16(new Float32Array([0, 1, -1, 0.5, -0.5, 2, -2]));
    expect(out[0]).toBe(0);
    expect(out[1]).toBe(32767); // +1 → 0x7fff
    expect(out[2]).toBe(-32768); // -1 → -0x8000
    // Int16Array assignment truncates toward zero (not rounds).
    expect(out[3]).toBe(Math.trunc(0.5 * 0x7fff)); // 16383.5 → 16383
    expect(out[4]).toBe(Math.trunc(-0.5 * 0x8000)); // -16384 exact
    expect(out[5]).toBe(32767); // clamped above +1
    expect(out[6]).toBe(-32768); // clamped below -1
  });

  it('tags a frame with a 1-byte prefix + int16 LE payload', () => {
    const pcm = new Int16Array([1, -2, 258]); // 258 = 0x0102
    const buf = tagFrame(pcm, AEC_FAR);
    const view = new DataView(buf);
    expect(buf.byteLength).toBe(1 + pcm.byteLength);
    expect(view.getUint8(0)).toBe(AEC_FAR);
    // Payload is little-endian int16.
    expect(view.getInt16(1, true)).toBe(1);
    expect(view.getInt16(3, true)).toBe(-2);
    expect(view.getInt16(5, true)).toBe(258);
  });

  it('near and far tags are distinct', () => {
    expect(AEC_NEAR).toBe(0x00);
    expect(AEC_FAR).toBe(0x01);
    expect(AEC_NEAR).not.toBe(AEC_FAR);
  });

  it('frameFromFloat composes conversion + tagging', () => {
    const buf = frameFromFloat(new Float32Array([1, -1]), AEC_NEAR);
    const view = new DataView(buf);
    expect(view.getUint8(0)).toBe(AEC_NEAR);
    expect(view.getInt16(1, true)).toBe(32767);
    expect(view.getInt16(3, true)).toBe(-32768);
  });
});
