import { describe, expect, it } from 'vitest';
import { synthesisDelayHint } from './use-generate';

describe('synthesisDelayHint', () => {
  it('stays quiet while a stage is still in its expected window', () => {
    expect(synthesisDelayHint('transcribing', 3)).toBeNull();
    expect(synthesisDelayHint('generating', 10)).toBeNull();
    expect(synthesisDelayHint('preparing', 60)).toBeNull();
  });

  it('names why a take is still running once it is late for that stage', () => {
    expect(synthesisDelayHint('transcribing', 8)).toBe('synthesisState.hint_long_reference');
    expect(synthesisDelayHint('ranking', 12)).toBe('synthesisState.hint_ranking_reference');
    expect(synthesisDelayHint('loading', 25)).toBe('synthesisState.hint_cold_load');
    expect(synthesisDelayHint('generating', 45)).toBe('synthesisState.hint_slow_engine');
  });
});
