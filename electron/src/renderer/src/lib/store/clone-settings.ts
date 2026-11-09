import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import { debounce } from '@tanstack/react-pacer';
import { onStorageKey } from '@/lib/storage-events';

export const CLONE_SETTINGS_STORAGE_KEY = 'voicestudio.clone.settings.v1';
/**
 * Stored beside the settings once steps became quality-first. Payloads without
 * it predate the change and carry the old fast default (16) whether or not the
 * user ever touched the slider.
 */
const SCHEMA_FIELD = 'schema';
const SCHEMA = 2;
const LEGACY_DEFAULT_STEPS = 16;
/**
 * Device-local: this device already moved its own pre-change settings to best
 * quality. Cloud sync carries only the generation fields (never `schema`), so
 * values it pulls later are user choices and must not be migrated again.
 */
export const STEPS_MIGRATED_STORAGE_KEY = 'voicestudio.clone.steps-quality-first';
const PERSIST_DEBOUNCE_MS = 250;

export interface CloneSettings {
  text: string;
  /** Display name; 'Auto' is omitted on the wire. */
  language: string;
  refText: string;
  instruct: string;
  /** Optional IndexTTS2 emotion direction, separate from voice identity. */
  emotionMode: 'none' | 'text' | 'vector';
  emotionText: string;
  /** Recommended 0..0.6 strength for IndexTTS2 text emotion. */
  emotionAlpha: number;
  /** Eight weights: happy, angry, sad, afraid, disgusted, melancholic, surprised, calm. */
  emotionVector: number[];
  /** Written delivery direction, sent only to engines whose instruct steers a clone (#3422). */
  direction: string;
  /** Sampling steps; null = the active engine's best quality (the default). */
  steps: number | null;
  wavBits: 16 | 24 | 32;
  effectPreset: 'broadcast' | 'raw';
  /** Built-in voice id for preset engines; '' = the engine's default. */
  presetVoice: string;
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
  emotionMode: 'none',
  emotionText: '',
  emotionAlpha: 0.6,
  emotionVector: [0, 0, 0, 0, 0, 0, 0, 0],
  direction: '',
  // Quality first: each engine's best value until the user picks one.
  steps: null,
  wavBits: 16,
  effectPreset: 'broadcast',
  presetVoice: '',
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
  if (key === 'emotionMode') return value === 'none' || value === 'text' || value === 'vector';
  if (key === 'emotionText' || key === 'direction')
    return typeof value === 'string' && value.length <= 500;
  if (key === 'emotionVector')
    return (
      Array.isArray(value) &&
      value.length === 8 &&
      value.every((v) => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1)
    );
  if (key === 'emotionAlpha')
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 0.6;
  if (key === 'steps')
    return value === null || (typeof value === 'number' && Number.isFinite(value));
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
 *
 * A payload from before quality-first defaults holding the old default step
 * count is read as "never chosen" and moves to best quality; any other stored
 * value is the user's explicit choice and is kept. Pass `migrateLegacySteps:
 * false` once this device has migrated, so a synced 16 stays a choice.
 */
export function parsePersistedCloneSettings(
  raw: string | null | undefined,
  { migrateLegacySteps = true }: { migrateLegacySteps?: boolean } = {},
): CloneSettings {
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
  if (
    migrateLegacySteps &&
    record[SCHEMA_FIELD] !== SCHEMA &&
    accepted.steps === LEGACY_DEFAULT_STEPS
  )
    accepted.steps = null;
  return { ...DEFAULT_CLONE_SETTINGS, ...accepted };
}

/** The JSON stored for `settings`, marked with the current schema. */
export function serializeCloneSettings(settings: CloneSettings): string {
  return JSON.stringify({ ...settings, [SCHEMA_FIELD]: SCHEMA });
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
      localStorage.setItem(CLONE_SETTINGS_STORAGE_KEY, serializeCloneSettings(settings));
    }
  } catch {
    // Quota / privacy mode — the in-memory store is still authoritative.
  }
}

/** Hydrate once, migrating pre-quality-first steps only on this device's first start. */
function hydrate(): CloneSettings {
  let migrated = false;
  try {
    migrated = localStorage.getItem(STEPS_MIGRATED_STORAGE_KEY) === '1';
    localStorage.setItem(STEPS_MIGRATED_STORAGE_KEY, '1');
  } catch {
    // Without storage nothing persisted can be legacy either.
  }
  return parsePersistedCloneSettings(readStorage(), { migrateLegacySteps: !migrated });
}

export const cloneSettingsStore = new Store<CloneSettings>(hydrate());

const persist = debounce(writeStorage, { wait: PERSIST_DEBOUNCE_MS });
cloneSettingsStore.subscribe((settings) => persist(settings));
// A value pulled by Cloud sync (or written by another window) replaces the in-memory copy,
// so the next save never writes the old defaults back.
onStorageKey(CLONE_SETTINGS_STORAGE_KEY, () => {
  // Cloud sync and other windows deliver current values, never legacy defaults.
  const next = parsePersistedCloneSettings(readStorage(), { migrateLegacySteps: false });
  if (JSON.stringify(next) !== JSON.stringify(cloneSettingsStore.state))
    cloneSettingsStore.setState(() => next);
});

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
  const {
    cfg,
    speed,
    tShift,
    posTemp,
    classTemp,
    layerPenalty,
    denoise,
    postprocess,
    duration,
    emotionMode,
    emotionText,
    emotionAlpha,
    emotionVector,
  } = DEFAULT_CLONE_SETTINGS;
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
    emotionMode,
    emotionText,
    emotionAlpha,
    emotionVector: [...emotionVector],
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
    emotionMode: DEFAULT_CLONE_SETTINGS.emotionMode,
    emotionText: DEFAULT_CLONE_SETTINGS.emotionText,
    emotionAlpha: DEFAULT_CLONE_SETTINGS.emotionAlpha,
    emotionVector: [...DEFAULT_CLONE_SETTINGS.emotionVector],
  });
}
