import { useCallback, useRef, useState } from 'react';

import { responsiveShellWidth } from '../utils/uiScaleEngine';

export function shellSizeClassForWidth(width) {
  if (width <= 600) return 'shell-mini';
  if (width <= 1100) return 'shell-narrow';
  return '';
}

/**
 * Observe the real app shell rather than the viewport. The callback ref is
 * intentional: App mounts bootstrap/setup screens before `.app-container`, so
 * an effect that runs only on the first render never sees the shell at all.
 */
export default function useResponsiveShellSize(scale, engine) {
  const observerRef = useRef(/** @type {ResizeObserver | null} */ (null));
  const [shellWidth, setShellWidth] = useState(Infinity);

  const observeShell = useCallback((node) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (!node) return;

    const measure = () => setShellWidth(node.clientWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(measure);
    observer.observe(node);
    observerRef.current = observer;
  }, []);

  const visibleShellWidth = responsiveShellWidth(shellWidth, scale, engine);
  return {
    observeShell,
    shellSizeClass: shellSizeClassForWidth(visibleShellWidth),
  };
}
