import { isTauri } from './media';

/**
 * @param {string} message
 * @param {string} [title]
 * @param {{ okLabel?: string, cancelLabel?: string }} [opts]  Custom button
 *   labels — honored by the native Tauri dialog only; window.confirm always
 *   shows OK/Cancel, so the message must read correctly with those too.
 */
export async function askConfirm(message, title = 'Confirm', opts = {}) {
  if (isTauri) {
    const { confirm } = await import('@tauri-apps/plugin-dialog');
    return await confirm(message, { title, ...opts });
  }
  return Promise.resolve(window.confirm(message));
}
