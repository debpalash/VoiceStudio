import { useEffect, useState } from 'react';
import { getBridge } from '@/components/bridge';

/** Native (green-button) fullscreen state; false outside Electron and in bridges without it. */
export function useWindowFullscreen(): boolean {
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    const app = getBridge()?.app;
    if (!app?.onFullScreen) return;
    let live = true;
    let changed = false;
    // An event that lands before the initial answer is newer, so it wins.
    void app.isFullScreen?.().then((value) => live && !changed && setFullscreen(value)).catch(() => {});
    const off = app.onFullScreen((value) => {
      changed = true;
      setFullscreen(value);
    });
    return () => {
      live = false;
      off();
    };
  }, []);
  return fullscreen;
}
