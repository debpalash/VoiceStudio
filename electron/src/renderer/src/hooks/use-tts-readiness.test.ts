import { expect, it } from 'vitest';
import { isPresetOnly } from './use-tts-readiness';

it('treats an engine with presets but no cloning as preset-only', () => {
  expect(isPresetOnly({ supports_cloning: false, preset_voices: ['expr-voice-2-f'] })).toBe(true);
  expect(isPresetOnly({ supports_cloning: false, preset_voices: [] })).toBe(false);
  expect(isPresetOnly({ supports_cloning: true, preset_voices: ['a'] })).toBe(false);
  expect(isPresetOnly(null)).toBe(false);
});
