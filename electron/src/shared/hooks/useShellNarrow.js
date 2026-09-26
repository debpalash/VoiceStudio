import { useLayoutEffect, useState } from 'react';

/**
 * useShellNarrow — mirrors the app shell's own width breakpoints instead of
 * viewport @media queries (which fire at the wrong threshold whenever the UI
 * scale ≠ 1 — see the shellWidth ResizeObserver rationale in App.jsx). The
 * shell already publishes its effective width as `shell-narrow` / `shell-mini`
 * classes on `.app-container`, so we read those and track flips with a
 * MutationObserver. Outside an app shell (isolated render) defaults to wide.
 *
 * @param {React.RefObject<HTMLElement>} rootRef — ref to any element inside
 *   the app shell (the component's root).
 * @returns {boolean} true when the shell is narrow or mini.
 */
export default function useShellNarrow(rootRef) {
  const [narrow, setNarrow] = useState(false);
  useLayoutEffect(() => {
    const shell = rootRef.current?.closest('.app-container');
    if (!shell) return undefined;
    const read = () =>
      setNarrow(shell.classList.contains('shell-narrow') || shell.classList.contains('shell-mini'));
    read();
    const mo = new MutationObserver(read);
    mo.observe(shell, { attributes: true, attributeFilter: ['class'] });
    return () => mo.disconnect();
  }, [rootRef]);
  return narrow;
}
