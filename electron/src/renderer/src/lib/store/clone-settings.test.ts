import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const KEY = 'voicestudio.clone.settings.v1';

async function loadModule(): Promise<typeof import('./clone-settings')> {
  vi.resetModules();
  return import('./clone-settings');
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
});

describe('parsePersistedCloneSettings', () => {
  it('restores valid quality choices and discards invalid persisted enums', async () => {
    const { parsePersistedCloneSettings } = await loadModule();
    expect(parsePersistedCloneSettings('{"wavBits":32,"effectPreset":"raw"}')).toMatchObject({
      wavBits: 32,
      effectPreset: 'raw',
    });
    expect(parsePersistedCloneSettings('{"wavBits":64,"effectPreset":"unknown"}')).toMatchObject({
      wavBits: 16,
      effectPreset: 'broadcast',
    });
  });
  it('returns defaults for null, malformed JSON and non-objects', async () => {
    const { parsePersistedCloneSettings, DEFAULT_CLONE_SETTINGS } = await loadModule();
    expect(parsePersistedCloneSettings(null)).toEqual(DEFAULT_CLONE_SETTINGS);
    expect(parsePersistedCloneSettings('{not json')).toEqual(DEFAULT_CLONE_SETTINGS);
    expect(parsePersistedCloneSettings('[1,2]')).toEqual(DEFAULT_CLONE_SETTINGS);
    expect(parsePersistedCloneSettings('"str"')).toEqual(DEFAULT_CLONE_SETTINGS);
  });

  it('merges known fields and ignores unknown or mistyped ones', async () => {
    const { parsePersistedCloneSettings, DEFAULT_CLONE_SETTINGS } = await loadModule();
    const parsed = parsePersistedCloneSettings(
      JSON.stringify({
        text: 'hello',
        steps: 8,
        cfg: 'not a number',
        speed: Number.NaN,
        selectedProfileId: 'p1',
        autoPlay: 'yes',
        bogus: 1,
      }),
    );
    expect(parsed).toEqual({
      ...DEFAULT_CLONE_SETTINGS,
      text: 'hello',
      steps: 8,
      selectedProfileId: 'p1',
    });
    expect('bogus' in parsed).toBe(false);
  });
});

describe('cloneSettingsStore persistence', () => {
  it('hydrates from localStorage on module init', async () => {
    localStorage.setItem(KEY, JSON.stringify({ language: 'French', showOverrides: true }));
    const { cloneSettingsStore } = await loadModule();
    expect(cloneSettingsStore.state.language).toBe('French');
    expect(cloneSettingsStore.state.showOverrides).toBe(true);
    expect(cloneSettingsStore.state.steps).toBe(16);
  });

  it('survives malformed stored JSON', async () => {
    localStorage.setItem(KEY, '{{{');
    const { cloneSettingsStore, DEFAULT_CLONE_SETTINGS } = await loadModule();
    expect(cloneSettingsStore.state).toEqual(DEFAULT_CLONE_SETTINGS);
  });

  it('writes changes back, debounced', async () => {
    const { setCloneSetting, patchCloneSettings } = await loadModule();
    setCloneSetting('text', 'a');
    patchCloneSettings({ text: 'ab', speed: 1.2 });
    expect(localStorage.getItem(KEY)).toBeNull();
    await vi.advanceTimersByTimeAsync(300);
    const stored = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, unknown>;
    expect(stored.text).toBe('ab');
    expect(stored.speed).toBe(1.2);
  });

  it('resetOverrides restores only the production overrides', async () => {
    const { cloneSettingsStore, patchCloneSettings, resetOverrides, DEFAULT_CLONE_SETTINGS } =
      await loadModule();
    patchCloneSettings({ text: 'keep me', steps: 4, duration: '2', denoise: false });
    resetOverrides();
    expect(cloneSettingsStore.state.text).toBe('keep me');
    expect(cloneSettingsStore.state.steps).toBe(DEFAULT_CLONE_SETTINGS.steps);
    expect(cloneSettingsStore.state.duration).toBe('');
    expect(cloneSettingsStore.state.denoise).toBe(true);
  });

  it('resets voice controls without changing audio quality or the draft', async () => {
    const { cloneSettingsStore, patchCloneSettings, resetVoiceControls, DEFAULT_CLONE_SETTINGS } =
      await loadModule();
    const preserved = { text: 'Keep script', refText: 'Keep transcript', instruct: 'Keep style',
      language: 'French', selectedProfileId: 'voice-123', showOverrides: true, autoPlay: true,
      wavBits: 32 as const, effectPreset: 'raw' as const, steps: 64 };
    patchCloneSettings({ ...preserved, cfg: 4, speed: 1.5, tShift: 0.9, posTemp: 1,
      classTemp: 2, layerPenalty: 1, duration: '5', denoise: false, postprocess: false });
    resetVoiceControls();
    expect(cloneSettingsStore.state).toEqual({ ...DEFAULT_CLONE_SETTINGS, ...preserved });
    await vi.advanceTimersByTimeAsync(300);
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}')).toEqual(cloneSettingsStore.state);
  });

  it('setCloneSetting is a no-op for an unchanged value', async () => {
    const { cloneSettingsStore, setCloneSetting } = await loadModule();
    const before = cloneSettingsStore.state;
    setCloneSetting('steps', before.steps);
    expect(cloneSettingsStore.state).toBe(before);
  });
});
