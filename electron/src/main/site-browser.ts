import {
  BrowserWindow,
  WebContentsView,
  ipcMain,
  session,
  type IpcMainInvokeEvent,
} from 'electron';
import { assertTrustedMainFrame } from './trusted-ipc';
import { browserUrl, type SiteBrowserState, type SiteBrowserBounds } from '../shared/site-browser';
import { listInstalledBrowsers, openInBrowser } from './installed-browsers';
import { randomUUID } from 'node:crypto';

export const SITE_BROWSER_CHANNELS = [
  'open',
  'close',
  'state',
  'navigate',
  'command',
  'bounds',
  'installed',
  'openExternal',
] as const;

export function registerSiteBrowser(getMainWindow: () => BrowserWindow | null) {
  const windows = new Map<
    number,
    {
      window: BrowserWindow;
      view: WebContentsView;
      state: SiteBrowserState;
      bounds: SiteBrowserBounds;
      dispose: () => void;
    }
  >();
  const trusted = (event: IpcMainInvokeEvent, owner: BrowserWindow | null) =>
    assertTrustedMainFrame(event, owner, 'Untrusted browser request');
  const entryFor = (event: IpcMainInvokeEvent) => {
    const entry = windows.get(event.sender.id);
    trusted(event, entry?.window ?? null);
    return entry!;
  };
  const resize = (entry: ReturnType<typeof entryFor>) => {
    if (entry.window.isDestroyed()) return;
    const [width, height] = entry.window.getContentSize();
    const zoom = entry.window.webContents.getZoomFactor();
    const x = Math.max(0, Math.min(width, Math.round(entry.bounds.x * zoom)));
    const y = Math.max(0, Math.min(height, Math.round(entry.bounds.y * zoom)));
    const bounds = {
      x,
      y,
      width: Math.max(0, Math.min(width - x, Math.round(entry.bounds.width * zoom))),
      height: Math.max(0, Math.min(height - y, Math.round(entry.bounds.height * zoom))),
    };
    entry.view.setBounds(bounds);
    entry.view.setVisible(bounds.width > 0 && bounds.height > 0);
  };
  const navigate = (entry: ReturnType<typeof entryFor>, input: unknown) => {
    const url = browserUrl(input);
    entry.state.url = url;
    entry.state.error = false;
    void entry.view.webContents.loadURL(url).catch(() => {
      /* did-fail-load owns feedback */
    });
  };
  ipcMain.handle('site-browser:open', (event, rawUrl: unknown) => {
    const parent = getMainWindow();
    trusted(event, parent);
    const url = browserUrl(rawUrl);
    const existing = [...windows.values()][0];
    if (existing) {
      navigate(existing, url);
      existing.window.show();
      existing.window.focus();
      return;
    }
    const window = parent!;
    const isolated = session.fromPartition(`voicestudio-site-browser-${randomUUID()}`);
    isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    isolated.setPermissionCheckHandler(() => false);
    const view = new WebContentsView({
      webPreferences: {
        session: isolated,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        webSecurity: true,
        allowRunningInsecureContent: false,
        navigateOnDragDrop: false,
      },
    });
    const entry = {
      window,
      view,
      bounds: { x: 0, y: 0, width: 0, height: 0 },
      dispose: () => {},
      state: { url, title: '', loading: true, canGoBack: false, canGoForward: false, error: false },
    };
    const toolbarId = window.webContents.id;
    const toolbarContents = window.webContents;
    windows.set(toolbarId, entry);
    window.contentView.addChildView(view);
    view.setVisible(false);
    const publish = () => {
      if (
        window.isDestroyed() ||
        view.webContents.isDestroyed() ||
        windows.get(toolbarId) !== entry
      )
        return;
      const contents = view.webContents;
      entry.state = {
        ...entry.state,
        title: contents.getTitle(),
        loading: contents.isLoading(),
        canGoBack: contents.navigationHistory.canGoBack(),
        canGoForward: contents.navigationHistory.canGoForward(),
      };
      window.webContents.send('site-browser:changed', entry.state);
    };
    view.webContents.on('did-start-loading', publish);
    view.webContents.on('did-stop-loading', publish);
    view.webContents.on('did-navigate', (_event, target) => {
      entry.state.url = target;
      publish();
    });
    view.webContents.on('did-navigate-in-page', (_event, target, isMainFrame) => {
      if (isMainFrame) {
        entry.state.url = target;
        publish();
      }
    });
    view.webContents.on('page-title-updated', publish);
    view.webContents.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
      if (isMainFrame && code !== -3) {
        entry.state.error = true;
        publish();
      }
    });
    view.webContents.on('did-start-navigation', (_event, target, _inPlace, isMainFrame) => {
      if (isMainFrame) {
        entry.state.error = false;
        entry.state.url = target;
        publish();
      }
    });
    const allowWebNavigation = (event: { preventDefault(): void }, target: string) => {
      try {
        browserUrl(target);
      } catch {
        event.preventDefault();
      }
    };
    view.webContents.on('will-navigate', allowWebNavigation);
    view.webContents.on('will-redirect', allowWebNavigation);
    view.webContents.setWindowOpenHandler(({ url: target }) => {
      try {
        navigate(entry, target);
      } catch {
        /* Non-web schemes never execute. */
      }
      return { action: 'deny' };
    });
    view.webContents.on('render-process-gone', () => {
      entry.state.error = true;
      publish();
    });
    const onResize = () => resize(entry);
    const dispose = () => {
      if (!windows.delete(toolbarId)) return;
      window.removeListener('resize', onResize);
      window.removeListener('closed', dispose);
      toolbarContents.removeListener('did-start-loading', dispose);
      if (!window.isDestroyed()) window.contentView.removeChildView(view);
      if (!view.webContents.isDestroyed()) view.webContents.close({ waitForBeforeUnload: false });
      void isolated.clearStorageData().catch(() => {});
      void isolated.clearCache().catch(() => {});
      if (!window.isDestroyed() && !toolbarContents.isDestroyed()) {
        toolbarContents.send('site-browser:changed', null);
      }
    };
    entry.dispose = dispose;
    view.webContents.on('before-input-event', (event, input) => {
      if (input.type === 'keyDown' && input.key === 'Escape') {
        event.preventDefault();
        dispose();
      }
    });
    window.on('resize', onResize);
    window.once('closed', dispose);
    window.webContents.once('did-start-loading', dispose);
    publish();
    navigate(entry, url);
  });
  ipcMain.handle('site-browser:close', (event) => {
    trusted(event, getMainWindow());
    windows.get(event.sender.id)?.dispose();
  });
  ipcMain.handle('site-browser:state', (event) => {
    trusted(event, getMainWindow());
    return windows.get(event.sender.id)?.state ?? null;
  });
  ipcMain.handle('site-browser:navigate', (event, url: unknown) => navigate(entryFor(event), url));
  ipcMain.handle('site-browser:command', (event, action: unknown) => {
    const contents = entryFor(event).view.webContents;
    if (action === 'back' && contents.navigationHistory.canGoBack())
      contents.navigationHistory.goBack();
    else if (action === 'forward' && contents.navigationHistory.canGoForward())
      contents.navigationHistory.goForward();
    else if (action === 'reload') contents.reload();
    else if (action === 'stop') contents.stop();
    else if (action !== 'back' && action !== 'forward') throw new Error('Invalid browser command');
  });
  ipcMain.handle('site-browser:bounds', (event, rect: unknown) => {
    const entry = entryFor(event);
    if (
      !rect ||
      typeof rect !== 'object' ||
      !['x', 'y', 'width', 'height'].every(
        (key) =>
          typeof (rect as Record<string, unknown>)[key] === 'number' &&
          Number.isFinite((rect as Record<string, unknown>)[key]),
      )
    )
      throw new Error('Invalid browser bounds');
    const { x, y, width, height } = rect as SiteBrowserBounds;
    entry.bounds = { x, y, width, height };
    resize(entry);
  });
  ipcMain.handle('site-browser:installed', (event) => {
    entryFor(event);
    return listInstalledBrowsers();
  });
  ipcMain.handle('site-browser:openExternal', (event, id: unknown) =>
    openInBrowser(id, entryFor(event).state.url),
  );
}
