// @vitest-environment node
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { BackendSupervisor } from './backend';

const native = vi.hoisted(() => ({
  handlers: new Map<string, (event: unknown, raw: unknown) => Promise<unknown>>(),
  selection: { canceled: false, filePaths: [] as string[], bookmarks: ['file-grant'] },
  retained: false,
  authorize: vi.fn(async (_directory: string, path: string, _kind: string) => {
    if (!native.retained) throw new Error('File scope was not retained');
    return { authorization: 'a'.repeat(64), path };
  }),
}));
const owner = vi.hoisted(() => {
  const mainFrame = { url: 'app://voicestudio/index.html' };
  return { webContents: { mainFrame }, isMaximized: () => false };
});
vi.mock('electron', () => ({
  app: {},
  BrowserWindow: {},
  dialog: { showOpenDialog: vi.fn(async () => native.selection) },
  ipcMain: {
    handle: (channel: string, handler: never) => native.handlers.set(channel, handler),
    on: vi.fn(),
  },
  net: {},
  shell: {},
  systemPreferences: {},
}));
vi.mock('./site-browser', () => ({ registerSiteBrowser: vi.fn() }));
vi.mock('./media-authorization', async (original) => ({
  ...(await original<typeof import('./media-authorization')>()),
  authorizeMediaTool: native.authorize,
}));
vi.mock('./security-scoped-files', async (original) => {
  const actual = await original<typeof import('./security-scoped-files')>();
  return {
    ...actual,
    securityScopedDialogOptions: (options: Electron.OpenDialogOptions) =>
      actual.securityScopedDialogOptions(options, true),
    retainSecurityScopedDirectory: (picked: Electron.OpenDialogReturnValue) => {
      if (!picked.bookmarks?.[0]) throw new Error('Missing file grant');
      native.retained = true;
    },
  };
});
import { dialog } from 'electron';
import { CHANNELS, registerIpc } from './ipc';

beforeEach(() => {
  vi.clearAllMocks();
  native.retained = false;
  native.selection = {
    canceled: false,
    filePaths: [join(tmpdir(), 'media-tool')],
    bookmarks: ['file-grant'],
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ app_version: '0.5.7', data_dir: tmpdir() })),
  );
  registerIpc(
    {
      subscribe: () => () => {},
      baseUrl: 'http://127.0.0.1:3900',
      requestHeaders: () => ({}),
    } as unknown as BackendSupervisor,
    () => owner as never,
  );
});
afterEach(() => vi.unstubAllGlobals());

const event = { sender: owner.webContents, senderFrame: owner.webContents.mainFrame };
it.each(['ffmpeg', 'ffprobe'])(
  'retains the selected %s file grant before issuing its backend capability',
  async (kind) => {
    await expect(
      native.handlers.get(CHANNELS.filesAuthorizeMediaTool)!(event, kind),
    ).resolves.toEqual({ authorization: 'a'.repeat(64), path: native.selection.filePaths[0] });
    expect(dialog.showOpenDialog).toHaveBeenCalledWith(owner, {
      properties: ['openFile'],
      securityScopedBookmarks: true,
    });
    expect(native.authorize).toHaveBeenCalledWith(tmpdir(), native.selection.filePaths[0], kind);
  },
);
it('does not authorize a canceled selection', async () => {
  native.selection.canceled = true;
  expect(await native.handlers.get(CHANNELS.filesAuthorizeMediaTool)!(event, 'ffmpeg')).toBeNull();
  expect(native.authorize).not.toHaveBeenCalled();
});
it('does not issue a capability when the sandbox selection omits its grant', async () => {
  native.selection.bookmarks = [];
  await expect(
    native.handlers.get(CHANNELS.filesAuthorizeMediaTool)!(event, 'ffmpeg'),
  ).rejects.toThrow('Missing file grant');
  expect(native.authorize).not.toHaveBeenCalled();
});
