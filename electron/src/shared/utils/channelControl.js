// electron/src/shared/utils/channelControl.js
import { isTauri } from './updater';
import { normalizeChannel } from './updateChannel';

/** Set the update channel: update the store and persist via Tauri. Returns the normalized channel. */
export async function setChannel(store, ch) {
  const next = normalizeChannel(ch);
  store.setUpdateChannelValue(next);
  if (!isTauri()) return next;
  const { invoke } = await import('@tauri-apps/api/core');
  await invoke('set_update_channel', { channel: next });
  return next;
}

/** Read the persisted channel from Tauri into the store on boot. */
export async function syncChannel(store) {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    const ch = await invoke('get_update_channel');
    store.setUpdateChannelValue(ch);
  } catch {
    /* keep default */
  }
}
