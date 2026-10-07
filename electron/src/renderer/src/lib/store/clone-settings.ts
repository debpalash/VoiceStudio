import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import { debounce } from '@tanstack/react-pacer';

export const CLONE_SETTINGS_STORAGE_KEY = 'voicestudio.clone.settings.v1';
const PERSIST_DEBOUNCE_MS = 250;

export interface CloneSettings {
  text: string;
  /** Display name; 'Auto' is omitted on the wire. */
  language: string;
  refText: string;
  instruct: string;
  steps: number;
  wavBits: 16 | 24 | 32;
  effectPreset: 'broadcast' | 'raw';
  cfg: number;
  speed: number;
  tShift: number;
  posTemp: number;
  classTemp: number;
  layerPenalty: number;
  denoise: boolean;
  postprocess: boolean;
  /** '' = auto. */
  duration: string;
  showOverrides: boolean;
  selectedProfileId: string | null;
  autoPlay: boolean;
}

export const DEFAULT_CLONE_SETTINGS: CloneSettings = {
  text: '',
  language: 'Auto',
  refText: '',
  instruct: '',
  steps: 16, // ~16 avoids ODE destabilisation in the flow-matcher.
  wavBits: 16,
  effectPreset: 'broadcast',
  cfg: 2.0,
  speed: 1.0,
  tShift: 0.1,
  posTemp: 5.0,
  classTemp: 0.0,
  layerPenalty: 5.0,
  denoise: true,
  postprocess: true,
  duration: '',
  showOverrides: false,
  selectedProfileId: null,
  autoPlay: false,
};

/** Whether `value` is a valid stored value for the clone setting `key`. */
export function acceptsCloneSetting(key: keyof CloneSettings, value: unknown): boolean {
  if (key === 'wavBits') return value === 16 || value === 24 || value === 32;
  if (key === 'effectPreset') return value === 'broadcast' || value === 'raw';
  if (key === 'selectedProfileId') return value === null || typeof value === 'string';
  const expected = typeof DEFAULT_CLONE_SETTINGS[key];
  if (typeof value !== expected) return false;
  return expected !== 'number' || Number.isFinite(value);
}

/**
 * Merge a persisted JSON string over the defaults. Malformed JSON, non-object
 * payloads and fields of the wrong type are ignored field-by-field so an old
 * or hand-edited entry can never poison the form.
 */
export function parsePersistedCloneSettings(raw: string | null | undefined): CloneSettings {
  if (!raw) return { ...DEFAULT_CLONE_SETTINGS };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ...DEFAULT_CLONE_SETTINGS };
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ...DEFAULT_CLONE_SETTINGS };
  }
  const record = parsed as Record<string, unknown>;
  const accepted: Partial<CloneSettings> = {};
  for (const key of Object.keys(DEFAULT_CLONE_SETTINGS) as Array<keyof CloneSettings>) {
    if (key in record && acceptsCloneSetting(key, record[key])) {
      Object.assign(accepted, { [key]: record[key] });
    }
  }
  return { ...DEFAULT_CLONE_SETTINGS, ...accepted };
}

function readStorage(): string | null {
  try {
    return typeof localStorage === 'undefined'
      ? null
      : localStorage.getItem(CLONE_SETTINGS_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStorage(settings: CloneSettings): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(CLONE_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
    }
  } catch {
    // Quota / privacy mode — the in-memory store is still authoritative.
  }
}

export const cloneSettingsStore = new Store<CloneSettings>(
  parsePersistedCloneSettings(readStorage()),
);

const persist = debounce(writeStorage, { wait: PERSIST_DEBOUNCE_MS });
cloneSettingsStore.subscribe((settings) => persist(settings));

export function useCloneSettings(): CloneSettings {
  return useStore(cloneSettingsStore);
}

export function useCloneSetting<K extends keyof CloneSettings>(key: K): CloneSettings[K] {
  return useStore(cloneSettingsStore, (settings) => settings[key]);
}

export function setCloneSetting<K extends keyof CloneSettings>(
  key: K,
  value: CloneSettings[K],
): void {
  cloneSettingsStore.setState((prev) => (prev[key] === value ? prev : { ...prev, [key]: value }));
}

export function patchCloneSettings(partial: Partial<CloneSettings>): void {
  cloneSettingsStore.setState((prev) => ({ ...prev, ...partial }));
}

/** Restore next-take quality without touching voice controls or the draft. */
export function resetAudioQuality(): void {
  const { wavBits, steps, effectPreset } = DEFAULT_CLONE_SETTINGS;
  patchCloneSettings({ wavBits, steps, effectPreset });
}

/** Restore voice controls without changing next-take quality or the draft. */
export function resetVoiceControls(): void {
  const { cfg, speed, tShift, posTemp, classTemp, layerPenalty, denoise, postprocess, duration } =
    DEFAULT_CLONE_SETTINGS;
  patchCloneSettings({
    cfg,
    speed,
    tShift,
    posTemp,
    classTemp,
    layerPenalty,
    denoise,
    postprocess,
    duration,
  });
}

/** Reset the production overrides (steps, cfg, …, duration) to their defaults. */
export function resetOverrides(): void {
  const { steps, cfg, speed, tShift, posTemp, classTemp, layerPenalty, denoise, postprocess } =
    DEFAULT_CLONE_SETTINGS;
  patchCloneSettings({
    wavBits: DEFAULT_CLONE_SETTINGS.wavBits,
    effectPreset: DEFAULT_CLONE_SETTINGS.effectPreset,
    steps,
    cfg,
    speed,
    tShift,
    posTemp,
    classTemp,
    layerPenalty,
    denoise,
    postprocess,
    duration: DEFAULT_CLONE_SETTINGS.duration,
  });
}
