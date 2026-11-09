import { beforeEach, describe, expect, it } from 'vitest';
import {
  belowBestQuality,
  bestSamplingSteps,
  effectiveSamplingSteps,
  LEGACY_SAMPLING_STEPS,
} from './quality';
import {
  estimateRender,
  fasterPlan,
  fitRenderSpeed,
  isSlowRender,
  readRenderSamples,
  recordRenderSample,
  RENDER_SPEED_STORAGE_KEY,
  speedKey,
  stepLadder,
  type RenderSample,
} from './render-speed';

const sample = (
  steps: number,
  renderSeconds: number,
  chars = 100,
  audioSeconds = 6,
): RenderSample => ({
  chars,
  audioSeconds,
  renderSeconds,
  steps,
  at: 0,
});

beforeEach(() => localStorage.clear());

describe('best-quality steps', () => {
  it.each([
    ['omnivoice', 32],
    ['omnivoice-subprocess', 32],
    ['voxcpm2', 32],
    ['dots-tts', 32],
    ['supertonic3', 12],
  ])('%s resolves an untouched setting to %i', (engine, best) => {
    expect(bestSamplingSteps(engine)).toBe(best);
    expect(effectiveSamplingSteps(null, engine)).toBe(best);
    expect(belowBestQuality(null, engine)).toBe(false);
    expect(belowBestQuality(best / 2, engine)).toBe(true);
  });

  it('keeps explicit choices (clamped) and the legacy wire value for engines without steps', () => {
    expect(effectiveSamplingSteps(16, 'omnivoice')).toBe(16);
    expect(effectiveSamplingSteps(100, 'omnivoice')).toBe(64);
    expect(effectiveSamplingSteps(16, 'supertonic3')).toBe(12);
    expect(effectiveSamplingSteps(null, 'kittentts')).toBe(LEGACY_SAMPLING_STEPS);
    expect(belowBestQuality(8, 'kittentts')).toBe(false);
  });
});

describe('estimator', () => {
  it('scales a single measured step count with steps plus a fixed share', () => {
    // 0.36 s/char at 32 steps → b = 0.36 / 36, a = 4b.
    const samples = [sample(32, 36)];
    expect(estimateRender(samples, 100, 32)!.renderSeconds).toBeCloseTo(36);
    expect(estimateRender(samples, 200, 32)!.renderSeconds).toBeCloseTo(72);
    expect(estimateRender(samples, 100, 16)!.renderSeconds).toBeCloseTo(20);
    expect(estimateRender(samples, 100, 32)!.audioSeconds).toBeCloseTo(6);
  });

  it('fits the measured scaling once two step counts exist', () => {
    // 10 s fixed + 1 s per step per 100 chars.
    const samples = [sample(16, 26), sample(32, 42), sample(32, 42)];
    const fit = fitRenderSpeed(samples)!;
    expect(fit.a * 100).toBeCloseTo(10);
    expect(fit.b * 100).toBeCloseTo(1);
    expect(estimateRender(samples, 100, 8)!.renderSeconds).toBeCloseTo(18);
  });

  it('uses medians so one stalled take does not dominate', () => {
    const samples = [sample(32, 36), sample(32, 36), sample(32, 400)];
    expect(estimateRender(samples, 100, 32)!.renderSeconds).toBeCloseTo(36);
  });

  it('falls back to the prior when noise makes more steps look faster', () => {
    const fit = fitRenderSpeed([sample(16, 40), sample(32, 30)])!;
    expect(fit.b).toBeGreaterThan(0);
  });

  it('ignores tiny takes and has no estimate without measurements', () => {
    expect(estimateRender([], 100, 32)).toBeNull();
    expect(estimateRender([sample(32, 30, 5)], 100, 32)).toBeNull();
  });
});

describe('offer thresholds', () => {
  it('is slow beyond 45 s, or beyond 3x real time once a take reaches 15 s', () => {
    expect(isSlowRender({ renderSeconds: 45, audioSeconds: 60 })).toBe(true);
    expect(isSlowRender({ renderSeconds: 44, audioSeconds: 20 })).toBe(false);
    expect(isSlowRender({ renderSeconds: 30, audioSeconds: 10 })).toBe(true);
    expect(isSlowRender({ renderSeconds: 12, audioSeconds: 2 })).toBe(false);
  });
});

describe('downgrade ladder', () => {
  it.each([
    ['omnivoice', 32, [16, 8]],
    ['voxcpm2', 64, [32, 16, 8]],
    ['dots-tts', 20, [10, 8]],
    ['supertonic3', 12, [6, 5]],
    ['omnivoice', 8, []],
    ['kittentts', 16, []],
  ])('%s from %i halves to its floor', (engine, steps, rungs) => {
    expect(stepLadder(engine, steps)).toEqual(rungs);
  });

  it('picks the mildest rung that ends the slow wait', () => {
    // 120 s at 32 steps for 100 chars → 16 steps ≈ 66.7 s (still slow), 8 ≈ 40 s.
    const samples = [sample(32, 120, 100, 60)];
    expect(fasterPlan('omnivoice', 32, samples, 100)).toMatchObject({ from: 32, to: 8 });
    // 60 s → 16 steps ≈ 33 s is no longer slow, so stop there.
    const plan = fasterPlan('omnivoice', 32, [sample(32, 60, 100, 60)], 100)!;
    expect(plan).toMatchObject({ from: 32, to: 16 });
    expect(plan.renderSeconds).toBeCloseTo(33.3, 0);
  });

  it('offers the floor when nothing gets under the threshold, and nothing when gains are small', () => {
    const slow = [sample(32, 600, 100, 60)];
    expect(fasterPlan('omnivoice', 32, slow, 100)).toMatchObject({ to: 8 });
    // At the floor there is nothing left to lower.
    expect(fasterPlan('omnivoice', 8, slow, 100)).toBeNull();
    // Engines without a steps knob never get an offer.
    expect(fasterPlan('kittentts', 16, slow, 100)).toBeNull();
    // Fixed cost dominates: halving steps saves under 25 %.
    const flat = [sample(16, 95), sample(32, 100)];
    expect(fasterPlan('omnivoice', 32, flat, 100)).toBeNull();
  });
});

describe('local measurements', () => {
  it('stores per engine and device, newest last, capped', () => {
    const key = speedKey('omnivoice', 'cuda');
    expect(key).toBe('omnivoice|cuda');
    expect(speedKey('omnivoice', null)).toBe('omnivoice|auto');
    for (let i = 0; i < 15; i++) recordRenderSample(key, sample(32, i + 1));
    const stored = readRenderSamples(key);
    expect(stored).toHaveLength(12);
    expect(stored.at(-1)!.renderSeconds).toBe(15);
    expect(readRenderSamples(speedKey('omnivoice', 'cpu'))).toEqual([]);
  });

  it('skips tiny or invalid takes and survives corrupt storage', () => {
    const key = speedKey('voxcpm2', 'mps');
    recordRenderSample(key, sample(32, 10, 3));
    recordRenderSample(key, { ...sample(32, 10), renderSeconds: Number.NaN });
    expect(readRenderSamples(key)).toEqual([]);
    localStorage.setItem(RENDER_SPEED_STORAGE_KEY, '{oops');
    expect(readRenderSamples(key)).toEqual([]);
    localStorage.setItem(
      RENDER_SPEED_STORAGE_KEY,
      JSON.stringify({ [key]: [{ chars: 'x' }, sample(32, 9)] }),
    );
    expect(readRenderSamples(key)).toEqual([sample(32, 9)]);
  });
});
