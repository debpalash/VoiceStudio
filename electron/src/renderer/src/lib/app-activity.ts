import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';

export type AppActivityKind =
  | 'synthesis'
  | 'transcription'
  | 'dictation'
  | 'translation'
  | 'recording'
  | 'longform'
  | 'batch';

type ActivityCounts = Record<AppActivityKind, number>;

const empty = (): ActivityCounts => ({
  synthesis: 0,
  transcription: 0,
  dictation: 0,
  translation: 0,
  recording: 0,
  longform: 0,
  batch: 0,
});

export const appActivity = new Store<ActivityCounts>(empty());

/** Register interruptible work. The returned release function is idempotent. */
export function beginAppActivity(kind: AppActivityKind): () => void {
  appActivity.setState((counts) => ({ ...counts, [kind]: counts[kind] + 1 }));
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    appActivity.setState((counts) => ({
      ...counts,
      [kind]: Math.max(0, counts[kind] - 1),
    }));
  };
}

export function hasActiveAppWork(): boolean {
  return activeAppWorkCount() > 0;
}

/** How many interruptible tasks are running right now. */
export function activeAppWorkCount(): number {
  return Object.values(appActivity.state).reduce((total, count) => total + count, 0);
}

export function useAppActivityCount(): number {
  return useStore(appActivity, (counts) =>
    Object.values(counts).reduce((total, count) => total + count, 0),
  );
}

export function useAppActivities(): ActivityCounts {
  return useStore(appActivity, (counts) => counts);
}
