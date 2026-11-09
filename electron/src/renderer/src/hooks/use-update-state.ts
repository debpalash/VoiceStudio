import { useEffect, useRef, useState } from 'react';
import { getBridge } from '@/components/bridge';
import { progressDue, type ShownProgress } from '@/lib/update-format';
import type { UpdateState } from '../../../preload/index.d';

/** The desktop updater's state, live; null in the browser UI or before it answers. */
export function useUpdateState(): UpdateState | null {
  const [state, setState] = useState<UpdateState | null>(null);
  useEffect(() => {
    const updates = getBridge()?.updates;
    if (!updates) return;
    let active = true;
    // An event can arrive before the initial snapshot resolves; the event is newer.
    let heard = false;
    const unsubscribe = updates.onState((next) => {
      heard = true;
      setState(next);
    });
    void updates
      .getState()
      .then((next) => active && !heard && setState(next))
      .catch(() => {});
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  return state;
}

/**
 * `state` with download progress, speed and time left refreshed about once a
 * second or per whole percent (see progressDue); every other change passes
 * straight through.
 */
export function useThrottledUpdate(state: UpdateState | null): UpdateState | null {
  const shown = useRef<{ mark: ShownProgress; state: UpdateState } | null>(null);
  if (!state || state.status !== 'downloading') {
    shown.current = null;
    return state;
  }
  const next = { status: state.status, pct: state.progress };
  const now = Date.now();
  if (!shown.current || progressDue(shown.current.mark, next, now))
    shown.current = { mark: { ...next, at: now }, state };
  return shown.current.state;
}
