/**
 * Steps sent to engines whose adapters ignore them (and the pre-quality-first
 * default). Kept on the wire so those requests stay byte-identical.
 */
export const LEGACY_SAMPLING_STEPS = 16;

interface SamplingSpec {
  /** Slider range the adapter honours. */
  range: [number, number];
  /** Best-quality default for a user who never chose a value. */
  best: number;
  /** Lowest value a "faster settings" offer ever proposes. */
  floor: number;
}

// OmniVoice documents 32 steps as its quality preset (docs/generation-parameters.md);
// the flow-matching engines use the same value, beyond which returns diminish
// while time keeps doubling. Supertonic-3's adapter caps at 12.
const SAMPLING: Record<string, SamplingSpec> = {
  omnivoice: { range: [8, 64], best: 32, floor: 8 },
  'omnivoice-subprocess': { range: [8, 64], best: 32, floor: 8 },
  'omnivoice-gguf': { range: [8, 64], best: 32, floor: 8 },
  voxcpm2: { range: [8, 64], best: 32, floor: 8 },
  'dots-tts': { range: [8, 64], best: 32, floor: 8 },
  supertonic3: { range: [5, 12], best: 12, floor: 5 },
};

/** Engines whose adapters actually forward sampling steps to their model. */
export function samplingStepRange(engineId?: string | null): [number, number] | null {
  return SAMPLING[engineId ?? '']?.range ?? null;
}

/** The engine's best-quality step count, or null when it manages sampling itself. */
export function bestSamplingSteps(engineId?: string | null): number | null {
  return SAMPLING[engineId ?? '']?.best ?? null;
}

/** The lowest step count a faster-settings offer may propose. */
export function samplingStepFloor(engineId?: string | null): number | null {
  return SAMPLING[engineId ?? '']?.floor ?? null;
}

/**
 * Steps to send. `null` means "best quality for this engine" (the default);
 * an explicit choice is clamped to the engine's range.
 */
export function effectiveSamplingSteps(steps: number | null, engineId?: string | null): number {
  const spec = SAMPLING[engineId ?? ''];
  if (steps === null) return spec?.best ?? LEGACY_SAMPLING_STEPS;
  return spec ? Math.min(spec.range[1], Math.max(spec.range[0], steps)) : steps;
}

/** True when the next take renders below the engine's best quality. */
export function belowBestQuality(steps: number | null, engineId?: string | null): boolean {
  const best = bestSamplingSteps(engineId);
  return best !== null && effectiveSamplingSteps(steps, engineId) < best;
}
