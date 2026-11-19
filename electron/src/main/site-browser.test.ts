// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserWindow, WebContentsView } from 'electron';
import { registerSiteBrowser, SITE_BROWSER_CHANNELS } from './site-browser';
import { browserUrl } from '../shared/site-browser';

const mocks = vi.hoisted(() => ({
  handlers: new Map<string, (...args: any[]) => any>(),
  windows: [] as any[],
  views: [] as any[],
  partition: vi.fn(),
  external: vi.fn(),
  list: vi.fn(),
}));
vi.mock('./installed-browsers', () => ({
  listInstalledBrowsers: mocks.list,
  openInBrowser: mocks.external,
}));
vi.mock('electron', async () => {
  const { EventEmitter } = await import('node:events');
  let id = 0;
  class Contents extends EventEmitter {
    id = ++id;
    mainFrame = { url: 'app://voicestudio/index.html' };
    destroyed = false;
    navigationHistory = {
      canGoBack: vi.fn(() => true),
      canGoForward: vi.fn(() => false),
      goBack: vi.fn(),
      goForward: vi.fn(),
    };
    loadURL = vi.fn(async () => {});
    getURL = () => this.mainFrame.url;
    getTitle = () => 'Page title';
    getZoomFactor = vi.fn(() => 1);
    isLoading = () => false;
    isDestroyed = () => this.destroyed;
    close = vi.fn(() => {
      this.destroyed = true;
    });
    send = vi.fn();
    setWindowOpenHandler = vi.fn();
    reload = vi.fn();
    stop = vi.fn();
  }
  class Window extends EventEmitter {
    id = ++id;
    webContents = new Contents();
    contentView = { addChildView: vi.fn(), removeChildView: vi.fn() };
    destroyed = false;
    constructor(public options?: unknown) {
      super();
      mocks.windows.push(this);
    }
    getContentSize = () => [1000, 800];
    isDestroyed = () => this.destroyed;
    show = vi.fn();
    focus = vi.fn();
    loadURL = vi.fn(async () => {});
    destroy = vi.fn(() => {
      this.destroyed = true;
      this.emit('closed');
    });
  }
  class View {
    webContents = new Contents();
    constructor(public options: unknown) {
      mocks.views.push(this);
    }
    setBounds = vi.fn();
    setVisible = vi.fn();
  }
  return {
    BrowserWindow: Window,
    WebContentsView: View,
    ipcMain: {
      handle: (channel: string, handler: (...args: any[]) => any) =>
        mocks.handlers.set(channel, handler),
    },
    session: { fromPartition: mocks.partition },
  };
});

let parent: BrowserWindow;
const invoke = (name: string, owner: BrowserWindow | WebContentsView, ...args: unknown[]) =>
  mocks.handlers.get(`site-browser:${name}`)!(
    { sender: owner.webContents, senderFrame: owner.webContents.mainFrame },
    ...args,
  );
beforeEach(() => {
  vi.clearAllMocks();
  mocks.windows.length = 0;
  mocks.views.length = 0;
  mocks.handlers.clear();
  mocks.partition.mockReturnValue({
    setPermissionRequestHandler: vi.fn(),
    setPermissionCheckHandler: vi.fn(),
    clearStorageData: vi.fn(async () => {}),
    clearCache: vi.fn(async () => {}),
  });
  parent = new BrowserWindow();
  registerSiteBrowser(() => parent);
});

describe('website URL policy', () => {
  it('accepts web URLs and bare domains', () => {
    expect(browserUrl(' voicestudio.sh/docs ')).toBe('https://voicestudio.sh/docs');
    expect(browserUrl('http://localhost:8000/?x=1&y=2')).toBe('http://localhost:8000/?x=1&y=2');
    expect(browserUrl('http:example.com')).toBe('http://example.com/');
    expect(browserUrl('https:example.com')).toBe('https://example.com/');
  });
  it.each([
    ['localhost:8443', 'https://localhost:8443/'],
    ['studio-server:8443/path?x=1#preview', 'https://studio-server:8443/path?x=1#preview'],
    ['example.com:8443/docs', 'https://example.com:8443/docs'],
    ['127.0.0.1:8443', 'https://127.0.0.1:8443/'],
    ['[::1]:8443', 'https://[::1]:8443/'],
  ])('normalizes the scheme-less address %s', (input, expected) => {
    expect(browserUrl(input)).toBe(expected);
  });
  it.each([
    'javascript:alert(1)',
    'data:text/html,test',
    'file:///etc/passwd',
    'app://voicestudio',
    'example.com:65536',
    'https://user:pass@example.com:8443',
    'javascript://example.com:8443',
    'https://user:pass@site.test',
    '',
    'https://',
    null,
    'a'.repeat(8193),
  ])('rejects unsafe or invalid input %s', (input) => expect(() => browserUrl(input)).toThrow());
});

it('opens an isolated sandboxed view only after the main window requests it', () => {
  expect(mocks.views).toHaveLength(0);
  invoke('open', parent, 'https://voicestudio.sh');
  const child = parent,
    view = mocks.views[0];
  expect(mocks.windows).toHaveLength(1);
  expect(child.loadURL).not.toHaveBeenCalled();
  expect(parent.contentView.addChildView).toHaveBeenCalledWith(view);
  expect(view.options.webPreferences).toMatchObject({
    sandbox: true,
    nodeIntegration: false,
    contextIsolation: true,
    webSecurity: true,
  });
  expect(view.options.webPreferences.preload).toBeUndefined();
  expect(mocks.partition.mock.calls[0][0]).not.toContain('persist:');
  expect(view.webContents.loadURL).toHaveBeenCalledWith('https://voicestudio.sh/');
  const callback = vi.fn();
  mocks.partition.mock.results[0].value.setPermissionRequestHandler.mock.calls[0][0](
    null,
    'media',
    callback,
  );
  expect(callback).toHaveBeenCalledWith(false);
  invoke('bounds', child, { x: 280, y: 240, width: 680, height: 500 });
  expect(view.setBounds).toHaveBeenLastCalledWith({ x: 280, y: 240, width: 680, height: 500 });
  expect(view.setVisible).toHaveBeenLastCalledWith(true);
});

it('keeps the native view inside the measured modal at different zoom levels and rejects invalid bounds', () => {
  invoke('open', parent, 'https://voicestudio.sh');
  const view = mocks.views[0];
  expect(view.setVisible).toHaveBeenLastCalledWith(false);
  vi.mocked(parent.webContents.getZoomFactor).mockReturnValue(1.25);
  invoke('bounds', parent, { x: 220, y: 180, width: 400, height: 300 });
  expect(view.setBounds).toHaveBeenLastCalledWith({ x: 275, y: 225, width: 500, height: 375 });
  invoke('bounds', parent, { x: 700, y: 600, width: 900, height: 900 });
  expect(view.setBounds).toHaveBeenLastCalledWith({ x: 875, y: 750, width: 125, height: 50 });
  for (const rect of [
    null,
    {},
    { x: NaN, y: 1, width: 2, height: 3 },
    { x: 1, y: 2, width: Infinity, height: 3 },
  ])
    expect(() => invoke('bounds', parent, rect)).toThrow('Invalid browser bounds');
});

it('closes the modal with Escape while the remote page has keyboard focus', () => {
  invoke('open', parent, 'https://voicestudio.sh');
  const event = { preventDefault: vi.fn() };
  mocks.views[0].webContents.emit('before-input-event', event, { type: 'keyDown', key: 'Escape' });
  expect(event.preventDefault).toHaveBeenCalled();
  expect(invoke('state', parent)).toBeNull();
});

it('denies every browser IPC to remote content and subframes', () => {
  invoke('open', parent, 'https://voicestudio.sh');
  const view = mocks.views[0],
    child = parent;
  for (const name of SITE_BROWSER_CHANNELS) {
    expect(() => invoke(name, view, 'https://example.com')).toThrow('Untrusted browser request');
    expect(() =>
      mocks.handlers.get(`site-browser:${name}`)!({
        sender: child.webContents,
        senderFrame: { url: 'app://voicestudio/index.html' },
      }),
    ).toThrow('Untrusted browser request');
  }
});

it('reuses the browser, supports history, reports failures and opens the current page externally', () => {
  invoke('open', parent, 'https://voicestudio.sh');
  const child = parent,
    view = mocks.views[0];
  invoke('open', parent, 'https://example.com');
  expect(mocks.views).toHaveLength(1);
  expect(child.focus).toHaveBeenCalled();
  invoke('command', child, 'back');
  invoke('command', child, 'reload');
  expect(view.webContents.navigationHistory.goBack).toHaveBeenCalled();
  expect(view.webContents.reload).toHaveBeenCalled();
  invoke('navigate', child, 'https://offline.test');
  view.webContents.emit('did-start-navigation', {}, 'https://offline.test/', false, true);
  view.webContents.emit('did-fail-load', {}, -105, 'offline', 'https://offline.test/', true);
  expect(invoke('state', child)).toMatchObject({ url: 'https://offline.test/', error: true });
  invoke('openExternal', child, 'default');
  expect(mocks.external).toHaveBeenCalledWith('default', 'https://offline.test/');
  invoke('navigate', child, 'https://voicestudio.sh');
  expect(invoke('state', child).error).toBe(false);
});

it('blocks non-web navigations and popups, closes remote contents and removes parent listeners', () => {
  invoke('open', parent, 'https://voicestudio.sh');
  const child = parent,
    view = mocks.views[0];
  const event = { preventDefault: vi.fn() };
  view.webContents.emit('will-redirect', event, 'file:///secret');
  expect(event.preventDefault).toHaveBeenCalled();
  const popup = view.webContents.setWindowOpenHandler.mock.calls[0][0];
  for (const target of ['tel:5551234', 'data:1', 'localhost:8443']) {
    const navigation = { preventDefault: vi.fn() };
    view.webContents.emit('will-navigate', navigation, target);
    expect(navigation.preventDefault).toHaveBeenCalled();
    popup({ url: target });
    expect(view.webContents.loadURL).not.toHaveBeenCalledWith(expect.stringContaining(target));
  }
  expect(popup({ url: 'javascript:alert(1)' })).toEqual({ action: 'deny' });
  expect(popup({ url: 'https://example.com' })).toEqual({ action: 'deny' });
  expect(view.webContents.loadURL).toHaveBeenLastCalledWith('https://example.com/');
  invoke('close', child);
  expect(parent.isDestroyed()).toBe(false);
  expect(invoke('state', parent)).toBeNull();
  expect(parent.contentView.removeChildView).toHaveBeenCalledWith(view);
  expect(view.webContents.close).toHaveBeenCalledWith({ waitForBeforeUnload: false });
  expect(parent.listenerCount('closed')).toBe(0);
  invoke('open', parent, 'https://voicestudio.sh');
  parent.destroy();
  expect(mocks.views[1].webContents.close).toHaveBeenCalled();
});

it('disposes an embedded page on renderer reload without replacing the workspace URL', () => {
  expect(invoke('state', parent)).toBeNull();
  invoke('open', parent, 'https://voicestudio.sh');
  parent.webContents.emit('did-start-loading');
  expect(invoke('state', parent)).toBeNull();
  expect(mocks.views[0].webContents.close).toHaveBeenCalled();
  expect(parent.listenerCount('resize')).toBe(0);
  expect(parent.webContents.loadURL).not.toHaveBeenCalled();
});

it('navigates to a host and port entered in the address bar', () => {
  invoke('open', parent, 'https://voicestudio.sh');
  invoke('navigate', parent, 'localhost:8443/preview');
  expect(mocks.views[0].webContents.loadURL).toHaveBeenLastCalledWith(
    'https://localhost:8443/preview',
  );
});
