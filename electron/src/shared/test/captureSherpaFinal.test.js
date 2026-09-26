/**
 * CaptureWidget live-dictation state machine — pure helpers.
 *
 * `isSherpaModel` gates the raw-PCM streaming path; `classifySherpaFinal`
 * distinguishes a per-utterance commit from the authoritative EOF summary so we
 * paste each sentence live (committing on pauses) without re-pasting the summary
 * — the heart of the "text appears as you speak" behaviour.
 */
import { describe, it, expect } from 'vitest';
import {
  isSherpaModel,
  classifySherpaFinal,
  sherpaSummaryTail,
  aggregateDeliveryKind,
  computeTypeDelta,
  parsePasteError,
} from '../components/CaptureWidget';
import { frameRms, createWaveform } from '../components/captureWaveform';

describe('isSherpaModel', () => {
  it('matches the sherpa- dictation ids and nothing else', () => {
    expect(isSherpaModel('sherpa-parakeet-tdt-v3')).toBe(true);
    expect(isSherpaModel('sherpa-whisper-tiny')).toBe(true);
    expect(isSherpaModel('whisper-large-v3')).toBe(false);
    expect(isSherpaModel('')).toBe(false);
    expect(isSherpaModel(undefined)).toBe(false);
  });
});

describe('classifySherpaFinal', () => {
  it('uses the explicit utterance kind even when the text repeats', () => {
    expect(
      classifySherpaFinal({ type: 'final', final_kind: 'utterance', text: 'yes' }, ['yes']),
    ).toBe('utterance');
  });

  it('uses the explicit summary kind when EOF includes an uncommitted tail', () => {
    expect(
      classifySherpaFinal(
        { type: 'final', final_kind: 'summary', text: 'first sentence tail words' },
        ['first sentence'],
      ),
    ).toBe('summary');
  });

  it('finalises on an explicit empty summary', () => {
    expect(classifySherpaFinal({ type: 'final', final_kind: 'summary', text: '' }, [])).toBe(
      'terminator',
    );
  });

  it('ignores empty utterance and unknown final kinds', () => {
    expect(classifySherpaFinal({ final_kind: 'utterance', text: '' }, [])).toBe('ignore');
    expect(classifySherpaFinal({ text: 'legacy guess' }, [])).toBe('ignore');
  });
});

describe('sherpaSummaryTail', () => {
  it('returns only text not already committed', () => {
    expect(sherpaSummaryTail('First sentence. Tail words.', ['First sentence.'])).toBe(
      'Tail words.',
    );
  });

  it('returns the whole summary when no utterance was committed', () => {
    expect(sherpaSummaryTail('Offline final.', [])).toBe('Offline final.');
  });

  it('does not duplicate a summary already delivered in full', () => {
    expect(sherpaSummaryTail('One. Two.', ['One.', 'Two.'])).toBe('');
  });
});

describe('aggregateDeliveryKind', () => {
  it('keeps copied as the truthful session outcome', () => {
    expect(aggregateDeliveryKind('pasted', 'inserted')).toBe('inserted');
    expect(aggregateDeliveryKind('inserted', 'copied')).toBe('copied');
    expect(aggregateDeliveryKind('copied', 'inserted')).toBe('copied');
  });
});

describe('computeTypeDelta', () => {
  it('pure append: no backspaces, types only the new tail', () => {
    expect(computeTypeDelta('hello wor', 'hello world')).toEqual({
      backspaces: 0,
      text: 'ld',
      noop: false,
    });
  });

  it('types the whole string from empty', () => {
    expect(computeTypeDelta('', 'hello')).toEqual({
      backspaces: 0,
      text: 'hello',
      noop: false,
    });
  });

  it('no change is a noop (no keystrokes)', () => {
    expect(computeTypeDelta('hello', 'hello')).toEqual({
      backspaces: 0,
      text: '',
      noop: true,
    });
  });

  it('both empty is a noop', () => {
    expect(computeTypeDelta('', '')).toEqual({ backspaces: 0, text: '', noop: true });
  });

  it('handles null/undefined inputs as empty', () => {
    expect(computeTypeDelta(undefined, null)).toEqual({ backspaces: 0, text: '', noop: true });
    expect(computeTypeDelta(null, 'hi')).toEqual({ backspaces: 0, text: 'hi', noop: false });
  });

  it('recognizer self-correction: backspaces the revised tail then types the fix', () => {
    // "hello to" → "hello two": common prefix "hello t", retract "o", type "wo".
    expect(computeTypeDelta('hello to', 'hello two')).toEqual({
      backspaces: 1,
      text: 'wo',
      noop: false,
    });
  });

  it('full-word revision: "recognise" → "recognize"', () => {
    // common prefix "recogni", retract "se", type "ze".
    expect(computeTypeDelta('recognise', 'recognize')).toEqual({
      backspaces: 2,
      text: 'ze',
      noop: false,
    });
  });

  it('shorter revision retracts the extra chars and types nothing', () => {
    // "helloo" → "hello": retract one 'o', type nothing.
    expect(computeTypeDelta('helloo', 'hello')).toEqual({
      backspaces: 1,
      text: '',
      noop: false,
    });
  });

  it('a leading separator is just another typed prefix delta', () => {
    // First delta of a new utterance is seeded as " word".
    expect(computeTypeDelta('', ' world')).toEqual({
      backspaces: 0,
      text: ' world',
      noop: false,
    });
  });

  it('counts astral (emoji/CJK surrogate-pair) chars as single units', () => {
    // "ab😀" → "ab😁": the emoji is one code point — retract 1, type 1, not 2.
    expect(computeTypeDelta('ab😀', 'ab😁')).toEqual({
      backspaces: 1,
      text: '😁',
      noop: false,
    });
  });

  it('appends after a multibyte char without disturbing it', () => {
    expect(computeTypeDelta('café', 'café au')).toEqual({
      backspaces: 0,
      text: ' au',
      noop: false,
    });
  });
});

describe('parsePasteError', () => {
  it('splits the Rust kind prefixes off simulate_paste Err strings', () => {
    expect(parsePasteError('a11y: accessibility not granted')).toEqual({
      kind: 'a11y',
      message: 'accessibility not granted',
    });
    expect(parsePasteError('clipboard: could not restore')).toEqual({
      kind: 'clipboard',
      message: 'could not restore',
    });
    expect(parsePasteError('paste: key event failed')).toEqual({
      kind: 'paste',
      message: 'key event failed',
    });
    expect(parsePasteError('preflight: clipboard-only session')).toEqual({
      kind: 'preflight',
      message: 'clipboard-only session',
    });
  });

  it('accepts Error objects (Tauri invoke may reject with either shape)', () => {
    expect(parsePasteError(new Error('a11y: nope'))).toEqual({ kind: 'a11y', message: 'nope' });
  });

  it('falls back to a generic paste kind for unprefixed/unknown errors', () => {
    expect(parsePasteError('something exploded')).toEqual({
      kind: 'paste',
      message: 'something exploded',
    });
    expect(parsePasteError(undefined)).toEqual({ kind: 'paste', message: '' });
  });
});

describe('captureWaveform', () => {
  describe('frameRms', () => {
    it('is 0 for silence, empty frames, and missing frames', () => {
      expect(frameRms(new Float32Array(320))).toBe(0);
      expect(frameRms(new Float32Array(0))).toBe(0);
      expect(frameRms(undefined)).toBe(0);
    });

    it('is 1 for a full-scale square wave and clamps beyond it', () => {
      expect(frameRms(Float32Array.from({ length: 32 }, (_, i) => (i % 2 ? 1 : -1)))).toBe(1);
      expect(frameRms(new Float32Array(8).fill(2))).toBe(1); // out-of-range input clamps
    });

    it('normalises Int16Array frames like Float32 ones', () => {
      const f32 = new Float32Array(64).fill(0.5);
      const i16 = new Int16Array(64).fill(0.5 * 32768);
      expect(frameRms(i16)).toBeCloseTo(frameRms(f32), 5);
    });

    it('computes RMS (a half-scale sine-ish constant is its amplitude)', () => {
      expect(frameRms(new Float32Array(128).fill(0.25))).toBeCloseTo(0.25, 6);
    });
  });

  describe('createWaveform', () => {
    it('returns all-zero bars before any audio arrives', () => {
      expect(createWaveform().getBars(8)).toEqual(Array.from({ length: 8 }, () => 0));
    });

    it('reacts within the ~100 ms liveness budget (bars move after ≤2 frames)', () => {
      const wave = createWaveform();
      wave.push(new Float32Array(320).fill(0.5)); // one 20 ms frame of speech
      const bars = wave.getBars(4);
      expect(bars[3]).toBeGreaterThan(0.3); // newest bar clearly off the floor
    });

    it('fills bars oldest→newest with the newest level last', () => {
      const wave = createWaveform();
      wave.push(0.1);
      wave.push(1.0);
      const bars = wave.getBars(4);
      expect(bars[0]).toBe(0); // unfilled slots pad the left
      expect(bars[1]).toBe(0);
      expect(bars[3]).toBeGreaterThan(bars[2]); // rising signal → rising bars
    });

    it('smooths: attack is fast, release decays gradually (no flicker-to-zero)', () => {
      const wave = createWaveform();
      const loud = wave.push(1.0);
      const quiet1 = wave.push(0);
      const quiet2 = wave.push(0);
      expect(loud).toBeGreaterThan(0.5); // fast attack
      expect(quiet1).toBeGreaterThan(0); // does not slam to zero
      expect(quiet1).toBeLessThan(loud); // …but does fall
      expect(quiet2).toBeLessThan(quiet1); // monotonic decay
    });

    it('keeps every bar in [0, 1] even for hot input', () => {
      const wave = createWaveform();
      for (let i = 0; i < 100; i++) wave.push(5); // out-of-range levels clamp
      for (const v of wave.getBars(12)) {
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThanOrEqual(1);
      }
    });

    it('ring-wraps past capacity, keeping only the most recent levels', () => {
      const wave = createWaveform({ capacity: 4 });
      for (let i = 0; i < 3; i++) wave.push(1.0); // drive level up (peak ≈ 0.94)…
      for (let i = 0; i < 4; i++) wave.push(0); // …then overwrite the whole ring
      const bars = wave.getBars(4);
      expect(Math.max(...bars)).toBeLessThan(0.9); // the ≈0.97 peak bar rotated out
      // and what's left is the decaying tail, oldest→newest
      expect(bars[0]).toBeGreaterThan(bars[1]);
      expect(bars[2]).toBeGreaterThan(bars[3]);
    });

    it('reset() clears levels and the smoothing envelope', () => {
      const wave = createWaveform();
      wave.push(1.0);
      wave.reset();
      expect(wave.getBars(4)).toEqual([0, 0, 0, 0]);
      // envelope reset too: the next quiet frame starts from silence
      expect(wave.push(0)).toBe(0);
    });
  });
});
