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
  it('never restores a saved transcript edit with the take (#3358)', () => {
    rememberTake('take', { ...DEFAULT_CLONE_SETTINGS, refText: 'Words from another voice.' });
    expect(takeSettings(item).refText).toBe('');
  });
  it('uses known history fields when metadata is malformed or absent', () => {
    localStorage.setItem('voicestudio.take-settings.v1', '{');
    expect(takeSettings(item)).toEqual({
      text: item.text,
      language: 'French',
      instruct: '',
      selectedProfileId: 'voice',
      refText: '',
      emotionMode: 'none',
      emotionText: '',
      emotionAlpha: 0.6,
      emotionVector: [0, 0, 0, 0, 0, 0, 0, 0],
      direction: '',
    });
  });
  it('restores style and delivery direction separately (#3422)', () => {
    // CosyVoice received "warm, intimate" as one instruct; the snapshot keeps both parts.
    rememberTake('take', { ...DEFAULT_CLONE_SETTINGS, instruct: 'warm', direction: 'intimate' });
    expect(takeSettings({ ...item, instruct: 'warm, intimate' })).toMatchObject({
      instruct: 'warm',
      direction: 'intimate',
    });
    // Legacy history without a snapshot never invents a direction.
    localStorage.clear();
    expect(takeSettings({ ...item, instruct: 'warm, intimate' })).toMatchObject({
      instruct: 'warm, intimate',
      direction: '',
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
  it('reusing legacy take metadata clears emotion from an unrelated recording', async () => {
    // Old takes only saved a handful of controls: absent emotion must mean Off.
    localStorage.setItem(
      'voicestudio.take-settings.v1',
      JSON.stringify({
        take: { speed: 1.25, steps: 24, selectedProfileId: 'different-voice' },
      }),
    );
    patchCloneSettings({
      emotionMode: 'vector',
      emotionText: 'an unrelated angry line',
      emotionAlpha: 0.3,
      emotionVector: [0, 1, 0, 0, 0, 0, 0, 0],
      autoPlay: true,
    });
    await reuseTake(item);
    expect(cloneSettingsStore.state).toMatchObject({
      speed: 1.25,
      steps: 24,
      selectedProfileId: 'voice',
      emotionMode: 'none',
      emotionText: '',
      emotionAlpha: 0.6,
      emotionVector: [0, 0, 0, 0, 0, 0, 0, 0],
      autoPlay: true,
    });
  });
  it('restores emotion from new takes but not malformed historical fields', async () => {
    rememberTake('take', {
      ...DEFAULT_CLONE_SETTINGS,
      emotionMode: 'text',
      emotionText: 'gentle and hopeful',
      emotionAlpha: 0.25,
      emotionVector: [0, 0, 0.5, 0, 0, 0, 0, 0],
    });
    expect(takeSettings(item)).toMatchObject({
      emotionMode: 'text',
      emotionText: 'gentle and hopeful',
      emotionAlpha: 0.25,
    });
    const raw = JSON.parse(localStorage.getItem('voicestudio.take-settings.v1') || '{}');
    raw.take.emotionMode = 'invalid';
    raw.take.emotionText = 42;
    raw.take.emotionAlpha = 999;
    raw.take.emotionVector = [3];
    localStorage.setItem('voicestudio.take-settings.v1', JSON.stringify(raw));
    expect(takeSettings(item)).toMatchObject({
      emotionMode: 'none',
      emotionText: '',
      emotionAlpha: 0.6,
      emotionVector: [0, 0, 0, 0, 0, 0, 0, 0],
    });
  });
  it.each([
    ['missing text', { emotionMode: 'text' }],
    ['blank text', { emotionMode: 'text', emotionText: '   ' }],
    ['invalid text type', { emotionMode: 'text', emotionText: 99 }],
    ['oversized text', { emotionMode: 'text', emotionText: 'x'.repeat(501) }],
    ['missing vector', { emotionMode: 'vector' }],
    ['short vector', { emotionMode: 'vector', emotionVector: [0, 1] }],
    ['out-of-bounds vector', { emotionMode: 'vector', emotionVector: [2, 0, 0, 0, 0, 0, 0, 0] }],
    ['invalid mode', { emotionMode: 'invalid', emotionText: 'sad' }],
  ])(
    'resets corrupt %s conditioning without dropping unrelated take settings',
    async (_case, emotion) => {
      localStorage.setItem(
        'voicestudio.take-settings.v1',
        JSON.stringify({
          take: {
            speed: 1.45,
            steps: 24,
            emotionAlpha: 0.2,
            ...emotion,
          },
        }),
      );
      patchCloneSettings({
        emotionMode: 'text',
        emotionText: 'different take',
        emotionVector: [0, 1, 0, 0, 0, 0, 0, 0],
      });
      await reuseTake(item);
      expect(cloneSettingsStore.state).toMatchObject({
        emotionMode: 'none',
        emotionText: '',
        emotionAlpha: DEFAULT_CLONE_SETTINGS.emotionAlpha,
        emotionVector: DEFAULT_CLONE_SETTINGS.emotionVector,
        speed: 1.45,
        steps: 24,
        selectedProfileId: item.profile_id,
      });
    },
  );
  it('keeps valid vector conditioning together with its selected mode', () => {
    const vector = [0.4, 0.1, 0, 0, 0, 0.2, 0.1, 0.2];
    localStorage.setItem(
      'voicestudio.take-settings.v1',
      JSON.stringify({ take: { emotionMode: 'vector', emotionVector: vector, speed: 1.25 } }),
    );
    expect(takeSettings(item)).toMatchObject({
      emotionMode: 'vector',
      emotionVector: vector,
      speed: 1.25,
    });
  });
  it('restores every generation control the clone form stores', () => {
    const changed: CloneSettings = {
      text: 'ignored: history text wins',
      language: 'ignored',
      refText: 'reference words',
      instruct: 'ignored',
      emotionMode: 'text',
      emotionText: 'quietly relieved',
      emotionAlpha: 0.4,
      emotionVector: [0.2, 0, 0, 0, 0, 0, 0, 0],
      direction: 'nervous, then bold',
      steps: 32,
      wavBits: 24,
      effectPreset: 'raw',
      presetVoice: 'F3',
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
