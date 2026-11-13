import { useEffect, useRef } from 'react';

type VisibilityDoc = Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'>;

/**
 * Run `callback` every `ms` while the document is visible. A hidden or
 * minimised window has nobody to show the result to, so the timer is cleared
 * (zero wake-ups) and restarted on the next `visibilitychange`, firing once
 * straight away so the first visible frame is current. Returns a disposer.
 */
export function startVisibleInterval(
  callback: () => void,
  ms: number,
  doc: VisibilityDoc = document,
): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;
  let disposed = false;
  const stop = () => {
    if (timer !== null) clearInterval(timer);
    timer = null;
  };
  const start = () => {
    if (timer === null) timer = setInterval(callback, ms);
  };
  const onChange = () => {
    if (doc.visibilityState === 'hidden') stop();
    else if (timer === null && !disposed) {
      callback();
      // The callback may dispose of this interval; do not restart it then.
      if (!disposed) start();
    }
  };
  if (doc.visibilityState !== 'hidden') start();
  doc.addEventListener('visibilitychange', onChange);
  return () => {
    disposed = true;
    stop();
    doc.removeEventListener('visibilitychange', onChange);
  };
}

/** React wrapper: pass `active = false` (or `ms = null`) to stop polling entirely. */
export function useVisibleInterval(callback: () => void, ms: number | null, active = true): void {
  const saved = useRef(callback);
  saved.current = callback;
  useEffect(() => {
    if (!active || ms === null) return;
    return startVisibleInterval(() => saved.current(), ms);
  }, [ms, active]);
}
