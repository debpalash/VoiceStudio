import { beforeEach, describe, expect, it } from 'vitest';
import { cloneSettingsStore, DEFAULT_CLONE_SETTINGS, patchCloneSettings } from './clone-settings';
import { rememberTake, reuseTake, takeSettings } from './takes';
import { toGenerateForm } from '@/lib/api/generate';
import type { HistoryItem } from '@/lib/api/types';
const item = {
  id: 'take',
  text: 'original script',
  language: 'French',
  instruct: '',
  profile_id: 'voice',
} as HistoryItem;
beforeEach(() => {
  localStorage.clear();
  patchCloneSettings({ ...DEFAULT_CLONE_SETTINGS });
});
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
    [16, 'broadcast'],
    [24, 'raw'],
    [32, 'raw'],
  ] as const)('restores %i-bit %s quality through the next generation request', async (wavBits, effectPreset) => {
    rememberTake(item.id, { ...DEFAULT_CLONE_SETTINGS, wavBits, effectPreset, speed: 1.5 });
    patchCloneSettings({
      wavBits: wavBits === 16 ? 32 : 16,
      effectPreset: effectPreset === 'broadcast' ? 'raw' : 'broadcast',
      autoPlay: true,
      showOverrides: true,
    });
    await reuseTake(item);
    const settings = cloneSettingsStore.state;
    const body = toGenerateForm({ ...settings, profileId: settings.selectedProfileId });
    expect(body.get('wav_bits')).toBe(String(wavBits));
    expect(body.get('effect_preset')).toBe(effectPreset);
    expect(settings.speed).toBe(1.5);
    expect(settings.autoPlay).toBe(true);
    expect(settings.showOverrides).toBe(true);
  });
  it.each([
    null,
    JSON.stringify({ take: { steps: 24 } }),
    JSON.stringify({ take: { wavBits: 8, effectPreset: 'unknown' } }),
    JSON.stringify({ take: { wavBits: '24', effectPreset: false } }),
  ])('preserves current quality when stored quality is absent or invalid: %s', async (metadata) => {
    if (metadata !== null) localStorage.setItem('voicestudio.take-settings.v1', metadata);
    patchCloneSettings({ wavBits: 32, effectPreset: 'raw' });
    await reuseTake(item);
    expect(cloneSettingsStore.state.wavBits).toBe(32);
    expect(cloneSettingsStore.state.effectPreset).toBe('raw');
  });
});
