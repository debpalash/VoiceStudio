// @vitest-environment node
import { mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

/** Both native save handlers, driven through the real registered IPC code. */
const native = vi.hoisted(() => ({
  handlers: new Map<string, (event: unknown, raw: unknown) => Promise<unknown>>(),
  savePath: '',
  failWrites: false,
}));

vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  /** Simulate a disk that fills up halfway through any write. */
  const halfThenFail = async (
    write: (data: Uint8Array) => Promise<void>,
    data: string | Uint8Array,
  ) => {
    const bytes = typeof data === 'string' ? Buffer.from(data) : data;
    await write(bytes.subarray(0, bytes.length >> 1));
    throw Object.assign(new Error('no space left on device'), { code: 'ENOSPC' });
  };
  return {
    ...actual,
    writeFile: (async (path: string, data: string | Uint8Array, options?: unknown) =>
      native.failWrites
        ? halfThenFail((bytes) => actual.writeFile(path, bytes), data)
        : actual.writeFile(path, data, options as never)) as typeof actual.writeFile,
    open: (async (...args: Parameters<typeof actual.open>) => {
      const handle = await actual.open(...args);
      const write = handle.writeFile.bind(handle);
      handle.writeFile = (async (data: string | Uint8Array) =>
        native.failWrites ? halfThenFail((bytes) => write(bytes), data) : write(data)) as never;
      return handle;
    }) as typeof actual.open,
  };
});

const owner = vi.hoisted(() => {
  const mainFrame = { url: 'app://voicestudio/index.html' };
  const webContents = { mainFrame };
  return { webContents, isMaximized: () => false };
});

vi.mock('electron', () => ({
  app: { getPath: () => tmpdir(), exit: vi.fn() },
  BrowserWindow: { fromWebContents: () => owner, getAllWindows: () => [] },
  dialog: {
    showSaveDialog: vi.fn(async () => ({ canceled: false, filePath: native.savePath })),
  },
  ipcMain: {
    handle: (channel: string, handler: (event: unknown, raw: unknown) => Promise<unknown>) =>
      native.handlers.set(channel, handler),
    on: vi.fn(),
  },
  net: { fetch: vi.fn(async () => new Response('replacement audio bytes')) },
  shell: {},
  systemPreferences: {},
}));
vi.mock('./site-browser', () => ({ registerSiteBrowser: vi.fn() }));

import { CHANNELS, registerIpc } from './ipc';
import type { BackendSupervisor } from './backend';
import { decodeDownloadFailure } from '../shared/download-failure';

let directory: string;
const event = { sender: owner.webContents, senderFrame: owner.webContents.mainFrame };

beforeEach(async () => {
  directory = await mkdtemp(join(tmpdir(), 'voicestudio-save-'));
  native.savePath = join(directory, 'take.wav');
  native.failWrites = false;
  native.handlers.clear();
  registerIpc(
    {
      subscribe: () => () => {},
      baseUrl: 'http://127.0.0.1:3900',
      requestHeaders: () => ({}),
    } as unknown as BackendSupervisor,
    () => owner as never,
  );
});
afterEach(async () => {
  await rm(directory, { recursive: true, force: true });
});

const saves = [
  [
    'backend downloads',
    CHANNELS.filesSaveAudio,
    () => ({ url: '/api/audio/take.wav', suggestedName: 'take.wav' }),
  ],
  [
    'local data',
    CHANNELS.filesSaveData,
    () => ({ data: new TextEncoder().encode('replacement data bytes'), suggestedName: 'take.wav' }),
  ],
] as const;

it.each(saves)(
  'keeps an existing export intact when saving %s fails',
  async (_, channel, request) => {
    await writeFile(native.savePath, 'complete previous export');
    native.failWrites = true;
    await expect(native.handlers.get(channel)!(event, request())).rejects.toThrow('no space');
    expect(await readFile(native.savePath, 'utf8')).toBe('complete previous export');
    expect(await readdir(directory)).toEqual(['take.wav']);
  },
);

it.each(saves)(
  'replaces an existing export when saving %s succeeds',
  async (_, channel, request) => {
    await writeFile(native.savePath, 'complete previous export that is longer');
    await expect(native.handlers.get(channel)!(event, request())).resolves.toEqual({
      canceled: false,
      path: native.savePath,
    });
    expect(await readFile(native.savePath, 'utf8')).toMatch(/^replacement (audio|data) bytes$/);
    expect(await readdir(directory)).toEqual(['take.wav']);
  },
);

it('carries the backend error body when a backend download fails (#2616)', async () => {
  const { net } = await import('electron');
  const detail = {
    code: 'dub_background_unavailable',
    message: 'Separated background is incomplete',
  };
  vi.mocked(net.fetch).mockResolvedValueOnce(
    new Response(JSON.stringify({ detail }), { status: 409, statusText: 'Conflict' }),
  );
  await writeFile(native.savePath, 'complete previous export');
  const error = await native.handlers.get(CHANNELS.filesSaveAudio)!(event, {
    url: '/api/dub/export/x',
    suggestedName: 'take.wav',
  }).catch((cause: unknown) => cause);
  expect(error).toBeInstanceOf(Error);
  expect((error as Error).message).toMatch(/^Could not download the file \(HTTP 409\)/);
  expect(decodeDownloadFailure(error)).toEqual({
    status: 409,
    statusText: 'Conflict',
    body: JSON.stringify({ detail }),
  });
  expect(await readFile(native.savePath, 'utf8')).toBe('complete previous export');
});
