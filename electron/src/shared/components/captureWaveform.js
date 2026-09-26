/**
 * captureWaveform — pure ring-buffer model behind the capture pill's live
 * waveform. No Web Audio, no DOM: the widget feeds it the Float32 frames the
 * EXISTING micCapture AudioWorklet already emits (~20 ms each at 16 kHz) and
 * polls `getBars(n)` to draw. Kept pure so the envelope math is unit-testable
 * without an AudioContext (mirrors utils/aec/pcm.js).
 *
 *   const wave = createWaveform();
 *   wave.push(frame);   // per worklet frame (Float32Array | Int16Array | 0..1 number)
 *   wave.getBars(12);   // → 12 smoothed 0..1 bar heights, oldest → newest
 */

/**
 * RMS level of one PCM frame, in [0, 1]. Accepts Float32 samples in [-1, 1]
 * or Int16Array samples (normalised by 32768). Empty/absent frames are silence.
 */
export function frameRms(frame) {
  if (!frame || !frame.length) return 0;
  const scale = frame instanceof Int16Array ? 1 / 32768 : 1;
  let sum = 0;
  for (let i = 0; i < frame.length; i++) {
    const s = frame[i] * scale;
    sum += s * s;
  }
  const rms = Math.sqrt(sum / frame.length);
  return rms > 1 ? 1 : rms;
}

/**
 * Create a waveform ring buffer.
 *
 * `attack`/`release` are asymmetric EMA coefficients: a loud frame pulls the
 * envelope up fast (bars visibly move within a frame or two of speech — the
 * ~100 ms liveness budget), silence lets it fall smoothly instead of
 * flickering to zero between words.
 *
 * @param {{capacity?: number, attack?: number, release?: number}} opts
 * @returns {{push(frame: Float32Array|Int16Array|number): number,
 *            getBars(n: number): number[],
 *            reset(): void}}
 */
export function createWaveform({ capacity = 48, attack = 0.6, release = 0.25 } = {}) {
  const ring = new Float32Array(capacity);
  let head = 0; // next write index
  let count = 0; // total levels stored (caps at capacity)
  let level = 0; // smoothed envelope carried across pushes

  return {
    /** Ingest one frame (or a precomputed 0..1 RMS); returns the new level. */
    push(frame) {
      const rms = typeof frame === 'number' ? Math.min(Math.max(frame, 0), 1) : frameRms(frame);
      level += (rms > level ? attack : release) * (rms - level);
      ring[head] = level;
      head = (head + 1) % capacity;
      if (count < capacity) count++;
      return level;
    },

    /**
     * The most recent `n` levels as 0..1 bar heights, oldest → newest (newest
     * is the last entry, i.e. the right edge of the pill). Slots with no data
     * yet render as 0. Heights are sqrt-mapped so quiet-but-real speech
     * (RMS ≈ 0.05) still reads as movement.
     */
    getBars(n) {
      const bars = Array.from({ length: n }, () => 0);
      const take = Math.min(n, count);
      for (let i = 0; i < take; i++) {
        const idx = (head - 1 - i + capacity * 2) % capacity;
        bars[n - 1 - i] = Math.sqrt(ring[idx]);
      }
      return bars;
    },

    /** Clear all state (new recording session). */
    reset() {
      ring.fill(0);
      head = 0;
      count = 0;
      level = 0;
    },
  };
}
