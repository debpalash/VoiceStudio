import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import type { PlaybackKind } from '@/lib/audio/playback-kind';

/** A clip played in the app-wide bottom player (recent takes, project takes and renders). */
export interface NowPlaying {
  src: string;
  /** Playback slot name; the play buttons compare against it to show their state. */
  source: string;
  title: string;
  /** What produced the clip; the dock derives its label from this. */
  kind: PlaybackKind;
  /** Known length in seconds, shown until the audio's own metadata loads. */
  duration?: number | null;
  /** Bumped on every play so replaying the same clip restarts it. */
  nonce: number;
}

export const nowPlayingStore = new Store<NowPlaying | null>(null);

export function useNowPlaying(): NowPlaying | null {
  return useStore(nowPlayingStore);
}

export function playInDock(clip: Omit<NowPlaying, 'nonce'>): void {
  nowPlayingStore.setState((previous) => ({ ...clip, nonce: (previous?.nonce ?? 0) + 1 }));
}

/** Playback slot the dock's own player claims for a clip, distinct from inline players of it. */
export function dockSource(source: string): string {
  return 'dock:' + source;
}

/** True when `active` is this clip playing, in the dock or in an inline player. */
export function isClipPlaying(active: string | null, source: string): boolean {
  return active === source || active === dockSource(source);
}

export function closeDock(): void {
  nowPlayingStore.setState(() => null);
}
