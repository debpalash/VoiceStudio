// @vitest-environment node
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { beforeEach, expect, it, vi } from 'vitest';
import type { BackendSupervisor } from './backend';

const native = vi.hoisted(() => {
  const make = (maximized: boolean, fragment = '') => ({
    webContents: { mainFrame: { url: 'app://voicestudio/index.html' + fragment } },
    isMaximized: vi.fn(() => maximized),
    minimize: vi.fn(),
    close: vi.fn(),
    maximize: vi.fn(),
    unmaximize: vi.fn(),
  });
  return {
    primary: make(true),
    recorder: make(false, '#/capture'),
    secondary: make(true),
    main: null as unknown,
    detached: false,
    handlers: new Map<string, (event: unknown, raw?: unknown) => unknown>(),
    events: new Map<string, (event: unknown) => unknown>(),
    shell: {
      openExternal: vi.fn(async () => {}),
      openPath: vi.fn(async () => ''),
      showItemInFolder: vi.fn(),
    },
    write: vi.fn(async () => {}),
    dialog: vi.fn(),
  };
});
vi.mock('electron', () => ({
  app: { getPath: () => tmpdir(), exit: vi.fn() },
  BrowserWindow: {
    fromWebContents: (sender: unknown) =>
      native.detached
        ? null
        : ([native.primary, native.recorder, native.secondary].find(
            (win) => win.webContents === sender,
          ) ?? null),
    getAllWindows: () => [],
  },
  ipcMain: {
    handle: (name: string, handler: never) => native.handlers.set(name, handler),
    on: (name: string, handler: never) => native.events.set(name, handler),
  },
  shell: native.shell,
  dialog: { showSaveDialog: native.dialog },
  net: { fetch: vi.fn(async () => new Response('fixture audio')) },
  systemPreferences: {},
}));
vi.mock('./backend', () => ({ backendRoot: () => '/fixture/backend' }));
vi.mock('./site-browser', () => ({ registerSiteBrowser: vi.fn() }));
vi.mock('./replace-file', () => ({ replaceFile: native.write }));
vi.mock('./security-scoped-files', () => ({
  securityScopedDialogOptions: (options: unknown) => options,
  withSecurityScopedBookmark: async (_bookmark: unknown, work: () => Promise<unknown>) => work(),
  retainSecurityScopedDirectory: vi.fn(),
}));
import { CHANNELS, registerIpc } from './ipc';
const event = (win: typeof native.primary) => ({
  sender: win.webContents,
  senderFrame: win.webContents.mainFrame,
});
const invoke = (channel: string, sender: unknown, raw?: unknown) =>
  Promise.resolve().then(() => native.handlers.get(channel)!(sender, raw));
const privileged = [
  [CHANNELS.filesOpenExternal, 'https://fixture.invalid/docs'],
  [CHANNELS.filesOpenExternal, 'mailto:fixture@fixture.invalid'],
  [CHANNELS.filesRevealPath, join(tmpdir(), 'fixture.wav')],
] as const;

beforeEach(() => {
  vi.clearAllMocks();
  native.main = native.primary;
  native.detached = false;
  native.handlers.clear();
  native.events.clear();
  native.dialog.mockResolvedValue({ canceled: false, filePath: join(tmpdir(), 'owned-save.wav') });
  registerIpc(
    {
      subscribe: () => () => {},
      baseUrl: 'http://127.0.0.1:3900',
      requestHeaders: () => ({}),
      websocketUrl: (path: string) => 'ws://127.0.0.1:3900' + path,
    } as unknown as BackendSupervisor,
    () => native.main as never,
  );
});

it.each(privileged)('allows primary main-frame %s', async (channel, raw) => {
  await invoke(channel, event(native.primary), raw);
  expect(
    native.shell.openExternal.mock.calls.length +
      native.shell.openPath.mock.calls.length +
      native.shell.showItemInFolder.mock.calls.length,
  ).toBe(1);
});
it.each(
  (['recorder', 'secondary'] as const).flatMap((name) =>
    privileged.map(([channel, raw]) => [name, channel, raw] as const),
  ),
)('denies same-origin %s invoking privileged %s with %s', async (name, channel, raw) => {
  await expect(invoke(channel, event(native[name]), raw)).rejects.toThrow('Untrusted');
  expect(native.shell.openExternal).not.toHaveBeenCalled();
  expect(native.shell.openPath).not.toHaveBeenCalled();
  expect(native.shell.showItemInFolder).not.toHaveBeenCalled();
});
it.each(privileged)('denies %s when no primary window is registered', async (channel, raw) => {
  native.main = null;
  await expect(invoke(channel, event(native.recorder), raw)).rejects.toThrow('Untrusted');
  expect(native.shell.openExternal).not.toHaveBeenCalled();
  expect(native.shell.openPath).not.toHaveBeenCalled();
  expect(native.shell.showItemInFolder).not.toHaveBeenCalled();
});
it.each(['primary', 'recorder', 'secondary'] as const)(
  'rejects subframes in %s without OS effects',
  async (name) => {
    const win = native[name];
    const subframe = {
      sender: win.webContents,
      senderFrame: { url: win.webContents.mainFrame.url },
    };
    for (const [channel, raw] of privileged)
      await expect(invoke(channel, subframe, raw)).rejects.toThrow('Untrusted');
    await expect(invoke(CHANNELS.windowIsMaximized, subframe)).rejects.toThrow('Untrusted');
    expect(native.shell.openExternal).not.toHaveBeenCalled();
    expect(native.shell.openPath).not.toHaveBeenCalled();
    expect(native.shell.showItemInFolder).not.toHaveBeenCalled();
  },
);
it('denies missing window owners and foreign sender-frame pairs', async () => {
  const missing = {
    sender: { mainFrame: native.primary.webContents.mainFrame },
    senderFrame: native.primary.webContents.mainFrame,
  };
  const foreign = {
    sender: native.recorder.webContents,
    senderFrame: native.primary.webContents.mainFrame,
  };
  for (const invalid of [missing, foreign]) {
    for (const [channel, raw] of privileged)
      await expect(invoke(channel, invalid, raw)).rejects.toThrow('Untrusted');
    await expect(invoke(CHANNELS.windowIsMaximized, invalid)).rejects.toThrow('Untrusted');
  }
});
it('keeps maximize state and controls local to a legitimate recorder', async () => {
  const recorder = event(native.recorder);
  await expect(invoke(CHANNELS.windowIsMaximized, recorder)).resolves.toBe(false);
  await expect(invoke(CHANNELS.windowIsMaximized, event(native.primary))).resolves.toBe(true);
  native.events.get(CHANNELS.windowMinimize)!(recorder);
  native.events.get(CHANNELS.windowClose)!(recorder);
  expect(native.recorder.minimize).toHaveBeenCalledOnce();
  expect(native.recorder.close).toHaveBeenCalledOnce();
  expect(native.primary.minimize).not.toHaveBeenCalled();
  expect(native.primary.close).not.toHaveBeenCalled();
});
it('retains user-dialog recorder data/audio exports and read-only connection access', async () => {
  const recorder = event(native.recorder);
  await expect(
    invoke(CHANNELS.filesSaveData, recorder, {
      data: new Uint8Array([1, 2]),
      suggestedName: 'fixture.txt',
    }),
  ).resolves.toMatchObject({ canceled: false });
  await expect(
    invoke(CHANNELS.filesSaveAudio, recorder, {
      url: '/api/audio/fixture.wav',
      suggestedName: 'fixture.wav',
    }),
  ).resolves.toMatchObject({ canceled: false });
  expect(native.dialog.mock.calls.every((call) => call[0] === native.recorder)).toBe(true);
  expect(native.write).toHaveBeenCalledTimes(2);
  await expect(invoke(CHANNELS.backendWebsocketUrl, recorder, '/ws/transcribe')).resolves.toBe(
    'ws://127.0.0.1:3900/ws/transcribe',
  );
});

it('does not substitute a primary window for missing caller ownership in a local query', async () => {
  native.detached = true;
  await expect(invoke(CHANNELS.windowIsMaximized, event(native.primary))).rejects.toThrow(
    'Untrusted',
  );
  expect(native.primary.isMaximized).not.toHaveBeenCalled();
});

it('keeps each guarded invocation inside the current diagnostic operation wrapper', async () => {
  let epoch: string | null = null;
  const observations: (string | null)[] = [];
  registerIpc(
    { subscribe: () => () => {} } as unknown as BackendSupervisor,
    () => native.primary as never,
    async () => {},
    <T,>(work: () => T): T => {
      observations.push(epoch);
      return work();
    },
  );
  for (const [index, [channel, raw]] of privileged.entries()) {
    epoch = String(index).repeat(32);
    await invoke(channel, event(native.primary), raw);
  }
  epoch = null;
  await invoke(CHANNELS.windowIsMaximized, event(native.recorder));
  expect(observations).toEqual(['0'.repeat(32), '1'.repeat(32), '2'.repeat(32), null]);
});
