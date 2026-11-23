import { expect, it } from 'vitest';
import { trimWaveform, trimmedAudio } from './trim-audio';
const buffer = {
  sampleRate: 100,
  duration: 2,
  numberOfChannels: 2,
  getChannelData: (channel: number) => new Float32Array(200).fill(channel ? -0.25 : 0.75),
} as AudioBuffer;
it('exports the exact selected interval as mono PCM WAV', async () => {
  const blob = trimmedAudio(buffer, 0.5, 1.5);
  const bytes = await blob.arrayBuffer();
  const view = new DataView(bytes);
  expect(bytes.byteLength).toBe(44 + 100 * 2);
  expect(view.getUint16(22, true)).toBe(1);
  expect(view.getUint32(24, true)).toBe(100);
  expect(view.getInt16(44, true)).toBe(8191);
});
it.each([[0.28, 0.3], [0.56, 0.58]])('exports a 20 ms selection from %s to %s', (start, end) => {
  expect(trimmedAudio(buffer, start, end).size).toBe(44 + 2 * 2);
});
it('rejects invalid, empty and out-of-bounds selections', () => {
  for (const [start, end] of [
    [NaN, 1],
    [-1, 1],
    [1, 1],
    [0.28, 0.299],
    [2, 1],
    [0, 3],
  ])
    expect(() => trimmedAudio(buffer, start, end)).toThrow('Invalid trim');
});


it('draws the same downmix that the saved voice clip uses', async () => {
  const stereo = {
    ...buffer,
    getChannelData: (channel: number) =>
      new Float32Array(200).fill(channel === 0 ? 0 : 1),
  } as AudioBuffer;
  const peaks = trimWaveform(stereo);
  const view = new DataView(await trimmedAudio(stereo, 0, 2).arrayBuffer());
  expect(peaks).toHaveLength(200);
  expect(peaks[0]).toBe(0.5);
  expect(view.getInt16(44, true)).toBe(Math.trunc(peaks[0] * 0x7fff));
});
