import { useCallback, useRef, useState } from 'react';

/**
 * Tracks in-flight async actions by key so a repeat click on the same control
 * is ignored while its action runs, without blocking unrelated controls.
 * The pending state is always cleared when the action settles.
 */
export function usePendingKeys() {
  const active = useRef(new Set<string>());
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const run = useCallback(async (key: string, action: () => Promise<void>) => {
    if (active.current.has(key)) return;
    active.current.add(key);
    setPending(new Set(active.current));
    try {
      await action();
    } finally {
      active.current.delete(key);
      setPending(new Set(active.current));
    }
  }, []);
  return { pending, run };
}
