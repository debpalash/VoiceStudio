// #526: voice-design seed selection. The backend echoes the seed it used via
// the X-Seed response header; the UI stores it and, when "keep this seed" is
// on, reuses it so voice tweaks stay on the same base timbre instead of
// re-rolling a fresh random voice every synth.

/** torch.manual_seed accepts a wide range; we keep seeds in a positive 31-bit
 *  range so they're easy to display, store, and re-enter. */
export const MAX_SEED = 2147483647;

/**
 * Pick the seed for a design synth: the pinned seed when "keep this seed" is on
 * AND a valid integer is pinned, otherwise a fresh random one. `rng` is
 * injectable for tests.
 */
export function pickDesignSeed(keepSeed, designSeed, rng = Math.random) {
  if (keepSeed && Number.isInteger(designSeed)) return designSeed;
  return Math.floor(rng() * MAX_SEED);
}
