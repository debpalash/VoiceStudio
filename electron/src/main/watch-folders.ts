import { app, dialog, ipcMain, type BrowserWindow, type IpcMainInvokeEvent } from 'electron';
import { join } from 'node:path';
import { backendRoot } from './backend';
import { DictationOutputClient } from './dictation-output';
import { assertTrustedMainFrame } from './trusted-ipc';
import type { WatchSelection, WatchUpload } from '../preload/index.d';

/** A separate helper owns each watch: stopping a large upload cannot interrupt dictation. */
export function installWatchFolders(
  main: () => BrowserWindow | null,
  baseUrl: () => string,
  requestHeaders: () => Record<string, string> = () => ({}),
): () => void {
  let active: { client: DictationOutputClient; token: string } | null = null;
  let picking = false;
  let revision = 0;
  const stop = () => {
    revision++;
    active?.client.close();
    active = null;
  };
  const attached = new WeakSet<object>();
  const guard = (event: IpcMainInvokeEvent) => {
    const window = main();
    assertTrustedMainFrame(event, window, 'Untrusted watch request');
    if (!attached.has(window.webContents)) {
      attached.add(window.webContents);
      window.webContents.on('did-start-loading', stop);
      window.webContents.on('render-process-gone', stop);
      window.webContents.on('destroyed', stop);
    }
    return window;
  };
  const current = (token: string) => {
    if (!active || typeof token !== 'string' || token !== active.token)
      throw new Error('Watch folder is not authorized');
    return active.client;
  };
  ipcMain.handle('watch:pick', async (event) => {
    const window = guard(event);
    if (picking || active) return null;
    picking = true;
    const generation = revision;
    let client: DictationOutputClient | null = null;
    try {
      const picked = await dialog.showOpenDialog(window, { properties: ['openDirectory'] });
      if (
        picked.canceled ||
        !picked.filePaths[0] ||
        generation !== revision ||
        window.isDestroyed()
      )
        return null;
      const name = 'voicestudio-desktop-bridge' + (process.platform === 'win32' ? '.exe' : '');
      client = new DictationOutputClient(
        app.isPackaged
          ? join(process.resourcesPath, 'native', name)
          : join(backendRoot(), 'native', 'desktop-bridge', 'target', 'debug', name),
      );
      const selected = (await client.request({
        method: 'watch_register',
        path: picked.filePaths[0],
      })) as WatchSelection;
      if (generation !== revision || window.isDestroyed()) {
        client.close();
        return null;
      }
      active = { client, token: selected.token };
      return selected;
    } catch {
      client?.close();
      throw new Error('Watch folder could not start');
    } finally {
      picking = false;
    }
  });
  ipcMain.handle('watch:scan', async (event, token: string) => {
    guard(event);
    return current(token).request({ method: 'watch_scan', token });
  });
  ipcMain.handle('watch:enqueue', async (event, request: WatchUpload) => {
    guard(event);
    const client = current(request?.token);
    const { entry, langs, voiceId, preserveBg, token } = request;
    if (
      !entry ||
      typeof entry.name !== 'string' ||
      !Number.isSafeInteger(entry.size) ||
      entry.size < 0 ||
      !Number.isSafeInteger(entry.mtime) ||
      entry.mtime < 0 ||
      !Array.isArray(langs) ||
      !langs.length ||
      langs.length > 100 ||
      langs.some((lang) => typeof lang !== 'string' || !/^[a-zA-Z0-9_-]{1,30}$/.test(lang)) ||
      typeof voiceId !== 'string' ||
      voiceId.length > 200 ||
      typeof preserveBg !== 'boolean'
    )
      throw new Error('Invalid watch upload');
    const backend = new URL(baseUrl());
    if (
      !['http:', 'https:'].includes(backend.protocol) ||
      backend.username ||
      backend.password ||
      backend.search ||
      backend.hash ||
      !['', '/'].includes(backend.pathname)
    )
      throw new Error('Backend unavailable');
    const authorization = requestHeaders().Authorization || null;
    const reply = (await client.request({
      method: 'watch_enqueue',
      backend_url: backend.origin,
      authorization,
      token,
      name: entry.name,
      expected_size: entry.size,
      expected_mtime: entry.mtime,
      langs,
      voice_id: voiceId || null,
      preserve_bg: preserveBg,
    })) as { status: number };
    if (reply.status < 200 || reply.status >= 300) throw new Error('Watch upload failed');
  });
  ipcMain.handle('watch:stop', (event, token: string) => {
    guard(event);
    if (active?.token === token) stop();
  });
  // Reload/crash/close revokes folder capabilities, including a pending native picker.
  return () => {
    stop();
    for (const channel of ['watch:pick', 'watch:scan', 'watch:enqueue', 'watch:stop'])
      ipcMain.removeHandler(channel);
  };
}
