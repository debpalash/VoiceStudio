/**
 * Local render-speed memory and the "faster settings" offer it drives.
 *
 * After each take we keep characters, audio seconds, render seconds and the
 * sampling steps used, per engine and device, in localStorage only. Estimates
 * come from those measurements; nothing here changes a setting by itself.
 */
import { samplingStepFloor } from './quality';

export const RENDER_SPEED_STORAGE_KEY = 'voicestudio.render-speed.v1';
const MAX_SAMPLES = 12;
const MAX_KEYS = 24;
/** Short takes are dominated by fixed overhead and would inflate per-character cost. */
const MIN_CHARS = 20;
/**
 * With measurements at one step count only, a take's cost is modelled as the
 * steps plus a fixed per-character share worth this many steps (text encoding,
 * vocoder). A second step count replaces the prior with a fitted line.
 */
const PRIOR_FIXED_STEPS = 4;

/** Offer faster settings when a take is expected to take at least this long… */
export const SLOW_RENDER_SECONDS = 45;
/** …or when it renders this many times slower than real time… */
export const SLOW_REALTIME_FACTOR = 3;
/** …once it takes at least this long (a 3 s wait is never worth a quality drop). */
export const MIN_REALTIME_CHECK_SECONDS = 15;
/** A faster option must save at least this share of the time… */
export const MIN_SPEEDUP = 0.25;
/** …and at least this many seconds. */
export const MIN_SAVED_SECONDS = 5;

export interface RenderSample {
  chars: number;
  audioSeconds: number;
  renderSeconds: number;
  steps: number;
  at: number;
}

export interface RenderEstimate {
  renderSeconds: number;
  audioSeconds: number;
}

export interface FasterPlan {
  from: number;
  to: number;
  renderSeconds: number;
}

export function speedKey(engineId: string, device?: string | null): string {
  return `${engineId}|${device || 'auto'}`;
}

const positive = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0;

function validSample(value: unknown): value is RenderSample {
  if (!value || typeof value !== 'object') return false;
  const sample = value as Record<string, unknown>;
  return (
    positive(sample.chars) &&
    positive(sample.audioSeconds) &&
    positive(sample.renderSeconds) &&
    positive(sample.steps) &&
    typeof sample.at === 'number'
  );
}

function readAll(): Record<string, RenderSample[]> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RENDER_SPEED_STORAGE_KEY) ?? '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: Record<string, RenderSample[]> = {};
    for (const [key, list] of Object.entries(parsed))
      if (Array.isArray(list)) out[key] = list.filter(validSample).slice(-MAX_SAMPLES);
    return out;
  } catch {
    return {};
  }
}

export function readRenderSamples(key: string): RenderSample[] {
  return readAll()[key] ?? [];
}

/** Remember one finished take; invalid or tiny takes are ignored. */
export function recordRenderSample(key: string, sample: RenderSample): void {
  if (!validSample(sample) || sample.chars < MIN_CHARS) return;
  const all = readAll();
  const list = [...(all[key] ?? []), sample].slice(-MAX_SAMPLES);
  delete all[key];
  // Most recently used keys last, so the oldest engine/device pairs drop first.
  const next = Object.fromEntries([...Object.entries(all), [key, list]].slice(-MAX_KEYS));
  try {
    localStorage.setItem(RENDER_SPEED_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* Quota or privacy mode: estimates simply stay unavailable. */
  }
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** Seconds per character as `a + b * steps`, fitted from the measurements. */
export function fitRenderSpeed(samples: RenderSample[]): { a: number; b: number } | null {
  const usable = samples.filter((sample) => validSample(sample) && sample.chars >= MIN_CHARS);
  if (!usable.length) return null;
  const groups = new Map<number, number[]>();
  for (const sample of usable) {
    const list = groups.get(sample.steps) ?? [];
    list.push(sample.renderSeconds / sample.chars);
    groups.set(sample.steps, list);
  }
  const points = [...groups].map(([steps, costs]) => ({
    steps,
    cost: median(costs),
    n: costs.length,
  }));
  if (points.length >= 2) {
    const meanX = points.reduce((sum, p) => sum + p.steps, 0) / points.length;
    const meanY = points.reduce((sum, p) => sum + p.cost, 0) / points.length;
    const cov = points.reduce((sum, p) => sum + (p.steps - meanX) * (p.cost - meanY), 0);
    const variance = points.reduce((sum, p) => sum + (p.steps - meanX) ** 2, 0);
    const b = cov / variance;
    // Noise can make more steps look faster; fall back to the prior then.
    if (b > 0) return { a: Math.max(0, meanY - b * meanX), b };
  }
  // The best-measured step count anchors the prior (latest wins a tie).
  const lastSteps = usable[usable.length - 1].steps;
  const anchor = points.reduce((best, p) =>
    p.n > best.n || (p.n === best.n && p.steps === lastSteps) ? p : best,
  );
  const b = anchor.cost / (anchor.steps + PRIOR_FIXED_STEPS);
  return { a: PRIOR_FIXED_STEPS * b, b };
}

/** Expected render and audio seconds for `chars` characters at `steps`. */
export function estimateRender(
  samples: RenderSample[],
  chars: number,
  steps: number,
): RenderEstimate | null {
  const fit = fitRenderSpeed(samples);
  if (!fit || chars <= 0) return null;
  const usable = samples.filter((sample) => validSample(sample) && sample.chars >= MIN_CHARS);
  return {
    renderSeconds: chars * (fit.a + fit.b * steps),
    audioSeconds: chars * median(usable.map((sample) => sample.audioSeconds / sample.chars)),
  };
}

/** Long enough, or slow enough relative to the audio, to be worth asking about. */
export function isSlowRender({ renderSeconds, audioSeconds }: RenderEstimate): boolean {
  return (
    renderSeconds >= SLOW_RENDER_SECONDS ||
    (renderSeconds >= MIN_REALTIME_CHECK_SECONDS &&
      renderSeconds >= SLOW_REALTIME_FACTOR * audioSeconds)
  );
}

/** Halving steps from `steps` down to the engine's floor, mildest first. */
export function stepLadder(engineId: string | null | undefined, steps: number): number[] {
  const floor = samplingStepFloor(engineId);
  if (floor === null) return [];
  const rungs: number[] = [];
  for (let next = steps; next > floor;) {
    next = Math.max(floor, Math.floor(next / 2));
    rungs.push(next);
  }
  return rungs;
}

/**
 * The mildest step reduction that brings the take under the slow thresholds,
 * else the floor; null when no rung saves enough time to justify it.
 */
export function fasterPlan(
  engineId: string | null | undefined,
  steps: number,
  samples: RenderSample[],
  chars: number,
): FasterPlan | null {
  const current = estimateRender(samples, chars, steps);
  if (!current) return null;
  const options = stepLadder(engineId, steps).map((to) => ({
    to,
    estimate: estimateRender(samples, chars, to)!,
  }));
  if (!options.length) return null;
  const choice = options.find((option) => !isSlowRender(option.estimate)) ?? options.at(-1)!;
  const saved = current.renderSeconds - choice.estimate.renderSeconds;
  if (saved < MIN_SAVED_SECONDS || saved < MIN_SPEEDUP * current.renderSeconds) return null;
  return { from: steps, to: choice.to, renderSeconds: choice.estimate.renderSeconds };
}
