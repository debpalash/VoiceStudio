/**
 * Tauri auto-update flow with progress + safety, channel-aware.
 *
 * The bundled updater plugin can't switch release channels from JS (its
 * endpoints are fixed in tauri.conf.json), so check + install go through the
 * Rust `check_update` / `install_update` commands, which bind the right
 * endpoints per call via `UpdaterExt`. The store contract here is identical to
 * the prior JS-plugin flow, so UpdateBadge / App.jsx are unaffected.
 *
 * - checkForUpdate(): non-blocking; on launch, surfaces availability into the
 *   store (no auto-install — the user picks when via the UpdateBadge).
 * - installUpdate(): installs with a progress callback (Rust emits
 *   `update://progress`), then relaunches. The badge gates the action while a
 *   job is running so in-flight work isn't lost.
 *
 * Both no-op outside a packaged Tauri build.
 */
import { normalizeChannel } from './updateChannel';
import { flushApplicationPersistence } from './persistenceLifecycle';

export function isTauri() {
  return typeof window !== 'undefined' && !!window.voicestudio;
}

async function currentChannel() {
  try {
    return normalizeChannel((await window.voicestudio.updates.getState()).channel);
  } catch {
    return 'stable';
  }
}

export async function checkForUpdate(store) {
  if (!isTauri()) return;
  // A periodic re-check must not interrupt an in-progress download or a
  // ready-to-restart state (it would reset the badge mid-flight), nor wipe a
  // surfaced error — `setUpdateChecking()` clears `updateError` and hides the
  // pill, so a 6h tick would silently erase the "failed · retry" prompt the
  // user still needs to act on. Retry is user-initiated (it goes through
  // installUpdate → downloading), so skipping the auto re-check here is safe.
  if (
    store.updateStatus === 'downloading' ||
    store.updateStatus === 'ready' ||
    store.updateStatus === 'error'
  ) {
    return;
  }
  try {
    store.setUpdateChecking();
    const channel = await currentChannel();
    await window.voicestudio.updates.setChannel(channel);
    const update = await window.voicestudio.updates.check();
    if (update.status === 'available' && update.availableVersion) {
      store.setUpdateAvailable(update.availableVersion, update.notes || null);
      // Announce it where the user is looking. The footer's version dot stays
      // as the persistent, non-intrusive marker; this is the one-time nudge.
      // Lazy so the toast never loads in a browser/dev build that can't update.
      //
      // Caught separately: a failed chunk load must not fall through to the
      // outer catch, which calls setUpdateIdle() and would erase the update we
      // just found. The announcement is the optional part — the available
      // state (footer dot, Settings → Updates) is what has to survive.
      try {
        const { showUpdateToast } = await import('../components/UpdateToast');
        showUpdateToast(update.availableVersion);
      } catch (e) {
        console.debug('Update toast failed to load (non-fatal):', e);
      }
    } else {
      store.setUpdateIdle();
    }
  } catch (e) {
    // Endpoint 404s until the first signed release on a channel — non-fatal.
    console.debug('Update check failed (non-fatal):', e);
    store.setUpdateIdle();
  }
}

export async function installUpdate(store) {
  if (!isTauri()) return;
  try {
    const channel = await currentChannel();
    await window.voicestudio.updates.setChannel(channel);
    store.setUpdateProgress(0);
    await window.voicestudio.updates.download();
    store.setUpdateReady();
    try {
      await flushApplicationPersistence();
    } catch (error) {
      // The update is already installed. Persistence failure must not rewrite
      // that successful state as an install error or strand the relaunch.
      console.warn('[persistence] installed-update flush failed', error);
    }
    await window.voicestudio.updates.install();
  } catch (e) {
    console.warn('Update install failed:', e);
    store.setUpdateError((e && e.message) || String(e) || 'Update failed');
  } finally {
    // Electron owns updater listeners and removes them with the window.
  }
}

/** Fetch the project's releases (changelog/history) via the Rust command. [] outside Tauri / on error. */
export async function listReleases(channel) {
  if (!isTauri()) return [];
  const data = await window.voicestudio.updates.listReleases(normalizeChannel(channel));
  return Array.isArray(data) ? data : [];
}

/** Current app version via Tauri, or null outside a packaged build. */
export async function fetchAppVersion() {
  if (!isTauri()) return null;
  return window.voicestudio.app.version;
}
