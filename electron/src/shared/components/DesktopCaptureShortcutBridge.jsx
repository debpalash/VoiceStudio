import { useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import {
  DEFAULT_SHORTCUT,
  eventMatchesShortcut,
  isShortcutRelease,
  parseShortcut,
} from '../utils/dictationShortcut';
import { requestDictationCapture } from '../utils/dictationCapture';

/** Focused-window fallback for desktop environments whose global shortcut
 * backend reports registration success but never delivers press events. */
export default function DesktopCaptureShortcutBridge() {
  const acceleratorRef = useRef(DEFAULT_SHORTCUT);
  const armedRef = useRef(null);

  useEffect(() => {
    if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return;
    let cancelled = false;
    let unlisten;

    const forward = (name) => {
      try {
        Promise.resolve(
          requestDictationCapture(name === 'tray-dictate-stop' ? 'stop' : 'start'),
        ).catch((error) => console.warn(`${name} fallback emit failed:`, error));
      } catch (error) {
        console.warn(`${name} fallback emit failed:`, error);
      }
    };

    const onKeyDown = (event) => {
      if (event.repeat || !eventMatchesShortcut(event, acceleratorRef.current)) return;
      event.preventDefault();
      armedRef.current = parseShortcut(acceleratorRef.current);
      forward('tray-dictate');
    };
    const onKeyUp = (event) => {
      if (!armedRef.current || !isShortcutRelease(event, armedRef.current)) return;
      event.preventDefault();
      armedRef.current = null;
      forward('tray-dictate-stop');
    };
    const onBlur = () => {
      if (!armedRef.current) return;
      armedRef.current = null;
      forward('tray-dictate-stop');
    };

    (async () => {
      try {
        const subscription = await listen('dictation-shortcut-changed', ({ payload }) => {
          if (payload?.accelerator) acceleratorRef.current = payload.accelerator;
        });
        if (cancelled) {
          subscription();
          return;
        }
        unlisten = subscription;
        const current = await invoke('get_effective_dictation_shortcut');
        if (!cancelled && current?.accelerator) acceleratorRef.current = current.accelerator;
        if (cancelled) unlisten?.();
      } catch (error) {
        console.warn('dictation shortcut fallback setup failed:', error);
      }
    })();

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);
    return () => {
      cancelled = true;
      unlisten?.();
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    };
  }, []);

  return null;
}
