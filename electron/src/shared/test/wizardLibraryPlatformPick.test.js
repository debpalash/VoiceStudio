import { describe, it, expect } from 'vitest';
import { isPlatformPick, isRecommendedPick } from '../components/WizardLibrary';

// The first-run "Models & engines" wizard surfaces platform-tuned optional
// models (e.g. MLX Whisper on Apple Silicon) by default and folds only the
// universal long tail. isPlatformPick is the predicate that drives that split.
describe('isPlatformPick — surface platform-tuned models by default', () => {
  const macTags = ['darwin', 'darwin-arm64'];

  it('matches a model explicitly tagged for this host', () => {
    expect(isPlatformPick({ platforms: ['darwin-arm64'] }, macTags)).toBe(true);
  });

  it('rejects a model tagged for a different platform', () => {
    expect(isPlatformPick({ platforms: ['cuda'] }, macTags)).toBe(false);
  });

  it('treats a universal model (no platforms field) as NOT a pick — it rides the fold', () => {
    expect(isPlatformPick({ repo_id: 'k2-fsa/OmniVoice' }, macTags)).toBe(false);
  });

  it('is safe with empty or absent platform tags', () => {
    expect(isPlatformPick({ platforms: ['darwin-arm64'] }, [])).toBe(false);
    expect(isPlatformPick({ platforms: ['darwin-arm64'] }, undefined)).toBe(false);
  });

  it('is safe with a malformed model', () => {
    expect(isPlatformPick(null, macTags)).toBe(false);
    expect(isPlatformPick({ platforms: 'darwin-arm64' }, macTags)).toBe(false); // string, not array
  });

  it('matches when ANY of the model platforms intersects the host tags', () => {
    expect(isPlatformPick({ platforms: ['cuda', 'darwin-arm64'] }, macTags)).toBe(true);
  });
});

// The wizard's "recommended" chip is driven by the backend's `curated` flag
// (curated_on in models.yaml — the same signal the Settings model store
// badges), with the platform-tag heuristic only as a legacy fallback.
describe('isRecommendedPick — curated flag drives the recommended chip', () => {
  const macTags = ['darwin', 'darwin-arm64'];

  it('marks a curated model recommended even without a platforms field', () => {
    expect(isRecommendedPick({ repo_id: 'a/b', curated: true }, macTags)).toBe(true);
  });

  it('a platform-matching but NOT-curated model is no longer a pick', () => {
    expect(isRecommendedPick({ platforms: ['darwin-arm64'], curated: false }, macTags)).toBe(false);
  });

  it('required models never wear the recommended chip (they are required)', () => {
    expect(isRecommendedPick({ required: true, curated: true }, macTags)).toBe(false);
  });

  it('falls back to the platform-tag heuristic when `curated` is absent (older backend)', () => {
    expect(isRecommendedPick({ platforms: ['darwin-arm64'] }, macTags)).toBe(true);
    expect(isRecommendedPick({ platforms: ['cuda'] }, macTags)).toBe(false);
  });

  it('is safe with malformed input', () => {
    expect(isRecommendedPick(null, macTags)).toBe(false);
    expect(isRecommendedPick(undefined, macTags)).toBe(false);
  });
});
