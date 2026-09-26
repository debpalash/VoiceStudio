import { describe, it, expect } from 'vitest';
import { transcribeStreamUrl } from './dub';

// #274: the optional speaker-count hint is appended only when it's a positive
// integer; otherwise the backend auto-detects.
describe('transcribeStreamUrl', () => {
  it('omits num_speakers when not provided', () => {
    expect(transcribeStreamUrl('job1')).toMatch(/\/dub\/transcribe-stream\/job1$/);
  });

  it('omits num_speakers for null / 0 / negative / NaN', () => {
    for (const v of [null, undefined, 0, -3, NaN] as (number | null | undefined)[]) {
      expect(transcribeStreamUrl('j', v)).not.toContain('num_speakers');
    }
  });

  it('appends a positive integer hint', () => {
    expect(transcribeStreamUrl('j', 3)).toContain('num_speakers=3');
  });

  it('floors a fractional hint', () => {
    expect(transcribeStreamUrl('j', 2.9)).toContain('num_speakers=2');
  });
});
