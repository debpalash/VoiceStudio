// @vitest-environment node
import { beforeEach, expect, it, vi } from 'vitest';

/**
 * `files:openExternal` is the one bridge call that hands a renderer-supplied
 * string to the OS. It must open web and mail links only: `file:` and a bare
 * absolute path both launch a local file through its OS association, which is
 * local code execution if the string came from a hostile backend reply. The
 * renderer has `files:revealPath` for showing a saved file.
 */
const native = vi.hoisted(() => ({
  handlers: new Map<string, (event: unknown, raw: unknown) => Promise<unknown>>(),
  openExternal: vi.fn(async () => {}),
  openPath: vi.fn(async () => ''),
}));

const owner = vi.hoisted(() => {
  const mainFrame = { url: 'app://voicestudio/index.html' };
  return { webContents: { mainFrame } };
});

vi.mock('electron', () => ({
  app: { getPath: () => '/tmp', exit: vi.fn(), getVersion: () => '0.0.0' },
  BrowserWindow: { fromWebContents: () => owner, getAllWindows: () => [] },
  ipcMain: {
    handle: (channel: string, handler: (event: unknown, raw: unknown) => Promise<unknown>) =>
      native.handlers.set(channel, handler),
    on: vi.fn(),
  },
  shell: { openExternal: native.openExternal, openPath: native.openPath },
  systemPreferences: {},
}));
vi.mock('./site-browser', () => ({ registerSiteBrowser: vi.fn() }));

import { CHANNELS, registerIpc } from './ipc';
import type { BackendSupervisor } from './backend';

const event = { sender: owner.webContents, senderFrame: owner.webContents.mainFrame };

function openExternal(target: string): Promise<unknown> {
  return native.handlers.get(CHANNELS.filesOpenExternal)!(event, target);
}

beforeEach(() => {
  native.handlers.clear();
  native.openExternal.mockClear();
  native.openPath.mockClear();
  registerIpc(
    { subscribe: () => () => {}, baseUrl: 'http://127.0.0.1:3900', requestHeaders: () => ({}) } as unknown as BackendSupervisor,
    () => owner as never,
  );
});

it.each([
  ['https://github.com/debpalash/VoiceStudio', 'https://github.com/debpalash/VoiceStudio'],
  ['http://127.0.0.1:3900/docs', 'http://127.0.0.1:3900/docs'],
  ['mailto:support@voicestudio.sh', 'mailto:support@voicestudio.sh'],
])('opens %s in the default browser', async (target, expected) => {
  await expect(openExternal(target)).resolves.toBeUndefined();
  expect(native.openExternal).toHaveBeenCalledWith(expected);
  expect(native.openPath).not.toHaveBeenCalled();
});

it.each([
  ['a file URL', 'file:///etc/passwd'],
  ['a local file URL', 'file:///C:/Windows/System32/calc.exe'],
  ['a POSIX path', '/etc/passwd'],
  ['a Windows path', 'C:\\Windows\\System32\\calc.exe'],
  ['a javascript URL', 'javascript:alert(1)'],
  ['a custom scheme', 'voicestudio://run'],
])('refuses %s', async (_label, target) => {
  await expect(openExternal(target)).rejects.toThrow(/Refusing to open/);
  expect(native.openExternal).not.toHaveBeenCalled();
  expect(native.openPath).not.toHaveBeenCalled();
});
