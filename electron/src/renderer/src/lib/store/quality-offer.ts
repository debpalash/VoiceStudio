import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import { recordRenderSample, speedKey, type RenderSample } from '@/lib/audio/render-speed';
import { setCloneSetting } from './clone-settings';

/** Engines whose faster-settings offer the user turned off ("Don't ask again"). */
export const QUALITY_OFFER_STORAGE_KEY = 'voicestudio.quality-offer.v1';

export interface MeasuredTake extends RenderSample {
  engineId: string;
  key: string;
}

export interface QualityOfferState {
  muted: string[];
  /** "Keep best quality" for this session, per engine. */
  dismissed: string[];
  /** The latest finished take, for the after-a-slow-take suggestion. */
  lastTake: MeasuredTake | null;
  /** Faster settings the user accepted from an offer this session. */
  applied: { engineId: string; from: number; to: number } | null;
  /** Bumped whenever a measurement is stored, so estimates re-read them. */
  version: number;
}

function readMuted(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(QUALITY_OFFER_STORAGE_KEY) ?? '{}');
    const muted = (parsed as { muted?: unknown } | null)?.muted;
    return Array.isArray(muted) ? muted.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

export const qualityOfferStore = new Store<QualityOfferState>({
  muted: readMuted(),
  dismissed: [],
  lastTake: null,
  applied: null,
  version: 0,
});

export function useQualityOffer(): QualityOfferState {
  return useStore(qualityOfferStore);
}

/** Store a finished take's speed (local only) and make it the latest take. */
export function recordTakeSpeed(
  engineId: string,
  device: string | null | undefined,
  sample: RenderSample,
): void {
  const key = speedKey(engineId, device);
  recordRenderSample(key, sample);
  qualityOfferStore.setState((prev) => ({
    ...prev,
    lastTake: { ...sample, engineId, key },
    version: prev.version + 1,
  }));
}

/** The user's click: lower refinement to `to`, remembering where it came from. */
export function applyFasterSettings(engineId: string, from: number, to: number): void {
  setCloneSetting('steps', to);
  qualityOfferStore.setState((prev) => ({
    ...prev,
    lastTake: null,
    // A further step keeps the original starting point, so Restore names it.
    applied: {
      engineId,
      from: prev.applied?.engineId === engineId ? prev.applied.from : from,
      to,
    },
  }));
}

/** Back to each engine's best quality. */
export function restoreBestQuality(): void {
  setCloneSetting('steps', null);
  qualityOfferStore.setState((prev) => ({ ...prev, applied: null }));
}

export function keepBestQuality(engineId: string): void {
  qualityOfferStore.setState((prev) => ({
    ...prev,
    lastTake: null,
    dismissed: prev.dismissed.includes(engineId) ? prev.dismissed : [...prev.dismissed, engineId],
  }));
}

export function dontAskAgain(engineId: string): void {
  const muted = qualityOfferStore.state.muted.includes(engineId)
    ? qualityOfferStore.state.muted
    : [...qualityOfferStore.state.muted, engineId];
  try {
    localStorage.setItem(QUALITY_OFFER_STORAGE_KEY, JSON.stringify({ muted }));
  } catch {
    /* Still muted for this session. */
  }
  qualityOfferStore.setState((prev) => ({ ...prev, muted, lastTake: null }));
}
