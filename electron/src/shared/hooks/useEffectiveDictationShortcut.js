import { useEffect, useState } from 'react';
import { DEFAULT_SHORTCUT, formatShortcut } from '../utils/dictationShortcut';

function fallbackInfo() {
  return {
    accelerator: DEFAULT_SHORTCUT,
    display: formatShortcut(DEFAULT_SHORTCUT),
    backend: 'focused',
  };
}

/** Live view of the shortcut the OS actually registered. */
export function useEffectiveDictationShortcut(enabled = undefined) {
  const desktop =
    enabled ??
    (typeof window !== 'undefined' &&
      Object.prototype.hasOwnProperty.call(window, '__TAURI_INTERNALS__'));
  const [state, setState] = useState(() => ({ info: fallbackInfo(), error: null }));

  useEffect(() => {
    if (!desktop) {
      setState({ info: fallbackInfo(), error: null });
      return;
    }
    let cancelled = false;
    let unlisten;
    const show = (info) => {
      if (!cancelled && info?.accelerator) setState({ info, error: null });
    };
    (async () => {
      try {
        const [{ invoke }, { listen }] = await Promise.all([
          import('@tauri-apps/api/core'),
          import('@tauri-apps/api/event'),
        ]);
        unlisten = await listen('dictation-shortcut-changed', ({ payload }) => show(payload));
        show(await invoke('get_effective_dictation_shortcut'));
        if (cancelled) unlisten?.();
      } catch (error) {
        if (!cancelled) setState((current) => ({ ...current, error }));
      }
    })();
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [desktop]);

  return state;
}
