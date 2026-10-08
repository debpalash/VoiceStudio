import { beforeEach, describe, expect, it } from 'vitest';
import { cloneSettingsStore, DEFAULT_CLONE_SETTINGS, patchCloneSettings } from './clone-settings';
import type { CloneSettings } from './clone-settings';
import { rememberTake, reuseTake, takeSettings } from './takes';
import type { HistoryItem } from '@/lib/api/types';
const item = {
  id: 'take',
  text: 'original script',
  language: 'French',
  instruct: '',
  profile_id: 'voice',
} as HistoryItem;
beforeEach(() => localStorage.clear());
describe('take metadata', () => {
  it('restores original generation controls without overwriting application preferences', () => {
    rememberTake('take', { ...DEFAULT_CLONE_SETTINGS, speed: 1.5, steps: 24, autoPlay: false });
    expect(takeSettings(item)).toMatchObject({
      text: 'original script',
      speed: 1.5,
      steps: 24,
      selectedProfileId: 'voice',
      language: 'French',
    });
    expect(takeSettings(item)).not.toHaveProperty('autoPlay');
  });
  it('uses known history fields when metadata is malformed or absent', () => {
    localStorage.setItem('voicestudio.take-settings.v1', '{');
    expect(takeSettings(item)).toEqual({
      text: item.text,
      language: 'French',
      instruct: '',
      selectedProfileId: 'voice',
    });
  });
  it.each([
    [24, 'raw'],
    [32, 'raw'],
  ] as const)('reuses a %i-bit %s take with its saved quality', async (wavBits, effectPreset) => {
    rememberTake('take', { ...DEFAULT_CLONE_SETTINGS, wavBits, effectPreset, speed: 1.25 });
    patchCloneSettings({ wavBits: 16, effectPreset: 'broadcast', speed: 1, autoPlay: true });
    await reuseTake(item);
    expect(cloneSettingsStore.state).toMatchObject({
      wavBits,
      effectPreset,
      speed: 1.25,
      autoPlay: true,
    });
  });
  it('restores every generation control the clone form stores', () => {
    const changed: CloneSettings = {
      text: 'ignored: history text wins',
      language: 'ignored',
      refText: 'reference words',
      instruct: 'ignored',
      steps: 32,
      wavBits: 24,
      effectPreset: 'raw',
      cfg: 3,
      speed: 0.8,
      tShift: 0.3,
      posTemp: 2,
      classTemp: 0.5,
      layerPenalty: 1,
      denoise: false,
      postprocess: false,
      duration: '12',
      showOverrides: true,
      selectedProfileId: 'ignored',
      autoPlay: true,
    };
    rememberTake('take', changed);
    const restored = takeSettings(item);
    for (const key of Object.keys(DEFAULT_CLONE_SETTINGS) as Array<keyof CloneSettings>) {
      if (key === 'autoPlay' || key === 'showOverrides') expect(restored).not.toHaveProperty(key);
      else expect(restored, key).toHaveProperty(key);
    }
  });
  it('ignores malformed stored quality values', () => {
    localStorage.setItem(
      'voicestudio.take-settings.v1',
      JSON.stringify({ take: { wavBits: 20, effectPreset: 'loud', speed: 'fast' } }),
    );
    const restored = takeSettings(item);
    expect(restored).not.toHaveProperty('wavBits');
    expect(restored).not.toHaveProperty('effectPreset');
    expect(restored).not.toHaveProperty('speed');
  });
});
