import { isTauri as _isTauri } from '../../utils/media';

/** True when running inside the Tauri webview (vs. vite dev / web preview / tests). */
export const isTauri = () => _isTauri;

// Tauri v2's webview disables native window.confirm/alert — they return
// false silently, making Delete/Reinstall buttons appear dead. Route through
// the dialog plugin when running in Tauri, fall back to browser confirm
// elsewhere (vite dev, tests).
export async function askConfirm(message, title = 'Confirm') {
  if (isTauri()) {
    const { ask } = await import('@tauri-apps/plugin-dialog');
    return ask(message, { title, kind: 'warning' });
  }
  return Promise.resolve(window.confirm(message));
}
