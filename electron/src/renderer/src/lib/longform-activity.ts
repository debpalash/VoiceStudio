import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';

/** The Stories or Audiobook render running now, as the rest of the app sees it. */
export interface LongformActivity {
  mode: 'stories' | 'audiobook';
  title: string;
  stage: string;
  completed: number;
  total: number;
  /** 0–100, from the per-chunk progress when the backend sends it. */
  percent: number;
}

/**
 * Published by the longform session so app-wide surfaces (the notifications
 * panel, the other longform page) can show and stop a render without loading
 * the editor. A render started on one page must never become invisible once
 * the user moves on.
 */
export const longformActivity = new Store<LongformActivity | null>(null);
let stopHandler: (() => void) | null = null;

export function publishLongformActivity(
  activity: LongformActivity | null,
  stop: (() => void) | null,
): void {
  stopHandler = stop;
  const current = longformActivity.state;
  if (
    current === activity ||
    (current &&
      activity &&
      current.mode === activity.mode &&
      current.title === activity.title &&
      current.stage === activity.stage &&
      current.completed === activity.completed &&
      current.total === activity.total &&
      current.percent === activity.percent)
  )
    return;
  longformActivity.setState(() => activity);
}

export function stopLongformActivity(): void {
  stopHandler?.();
}

export const useLongformActivity = () => useStore(longformActivity);

export const longformRoute = (mode: LongformActivity['mode']) =>
  mode === 'stories' ? ('/stories' as const) : ('/audiobook' as const);
