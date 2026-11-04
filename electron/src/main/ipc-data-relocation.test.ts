import { existsSync } from 'node:fs';
import {
  mkdtemp,
  mkdir,
  readFile,
  realpath,
  rename,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { BackendSupervisor } from './backend';

const fixture = vi.hoisted(() => {
  const mainFrame = { url: 'app://voicestudio/index.html' };
  const webContents = { mainFrame, isDestroyed: () => false, send: vi.fn() };
  return {
    owner: { webContents, isDestroyed: () => false, isMaximized: () => false },
    handlers: new Map<string, (event: unknown, raw?: unknown) => Promise<unknown>>(),
    picked: '',
    userData: '',
    remote: false,
    managed: true,
  };
});

vi.mock('electron', () => ({
  app: { getPath: () => fixture.userData, exit: vi.fn() },
  BrowserWindow: { fromWebContents: () => fixture.owner, getAllWindows: () => [] },
  ipcMain: {
    handle: (channel: string, handler: (event: unknown, raw?: unknown) => Promise<unknown>) =>
      fixture.handlers.set(channel, handler),
    on: vi.fn(),
  },
  dialog: { showOpenDialog: vi.fn(async () => ({ canceled: false, filePaths: [fixture.picked] })) },
  net: {},
  shell: {},
  systemPreferences: {},
}));
vi.mock('./site-browser', () => ({ registerSiteBrowser: vi.fn() }));

import { CHANNELS, registerIpc } from './ipc';
import { readDataDirectorySetting, writeDataDirectorySetting } from './data-relocation';

const event = {
  sender: fixture.owner.webContents,
  senderFrame: fixture.owner.webContents.mainFrame,
};
let root: string;
let source: string;
let target: string;
let alias: string;
let foreign: string;
let environment: string;
let duringVerification: (() => Promise<void>) | undefined;
let duringStart: (() => Promise<void>) | undefined;
let stop: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'VoiceStudio-ipc-relocation-'));
  source = join(root, 'source');
  target = join(root, 'destination', 'data');
  alias = join(root, 'picked-alias');
  foreign = join(root, 'foreign');
  environment = join(root, 'config', 'env');
  fixture.userData = join(root, 'user');
  await mkdir(join(source, 'voices'), { recursive: true });
  await mkdir(target, { recursive: true });
  await mkdir(join(foreign, 'data'), { recursive: true });
  await writeFile(join(source, 'voices', 'sample.wav'), 'owned voice');
  await writeFile(join(foreign, 'data', 'personal.txt'), 'foreign data');
  await symlink(
    join(root, 'destination'),
    alias,
    process.platform === 'win32' ? 'junction' : 'dir',
  );
  fixture.picked = join(alias, 'data');
  fixture.handlers.clear();
  fixture.remote = false;
  fixture.managed = true;
  duringVerification = undefined;
  duringStart = undefined;
  vi.stubEnv('OMNIVOICE_ENV_FILE', environment);
  await writeDataDirectorySetting(source);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      expect(url).toBe('http://127.0.0.1:3900/system/info');
      const current = await readDataDirectorySetting();
      if (current === target && duringVerification) {
        const operation = duringVerification;
        duringVerification = undefined;
        await operation();
      }
      return new Response(JSON.stringify({ app_version: '0.5.7', data_dir: current }));
    }),
  );
  stop = vi.fn(async () => undefined);
  registerIpc(
    {
      subscribe: () => () => {},
      baseUrl: 'http://127.0.0.1:3900',
      requestHeaders: () => ({}),
      get connection() {
        return { remote: fixture.remote };
      },
      get status() {
        return { managed: fixture.managed };
      },
      shutdown: stop,
      start: vi.fn(async () => {
        if (duringStart && (await readDataDirectorySetting()) === target) await duringStart();
      }),
    } as unknown as BackendSupervisor,
    () => fixture.owner as never,
  );
});

afterEach(async () => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  await rm(root, { recursive: true, force: true });
});

async function choose() {
  const selected = (await fixture.handlers.get(CHANNELS.maintenanceChooseDataDirectory)!(
    event,
  )) as {
    authorization: string;
    target: string;
  };
  expect(selected.target).toBe(await realpath(target));
  return selected.authorization;
}

async function retarget() {
  await rename(alias, join(root, 'original-alias'));
  await symlink(foreign, alias, process.platform === 'win32' ? 'junction' : 'dir');
}

it('rejects the picker alias retargeted during real IPC relocation verification', async () => {
  const authorization = await choose();
  duringVerification = retarget;
  await expect(
    fixture.handlers.get(CHANNELS.maintenanceRelocateDataDirectory)!(event, authorization),
  ).rejects.toMatchObject({ message: 'rollback_incomplete', cause: { message: 'target_changed' } });
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('owned voice');
  expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('owned voice');
  expect(await readFile(join(foreign, 'data', 'personal.txt'), 'utf8')).toBe('foreign data');
  expect(await readDataDirectorySetting()).toBe(source);
});

it('cannot authorize a different physical destination by retargeting before IPC confirmation', async () => {
  const authorization = await choose();
  await retarget();
  await expect(
    fixture.handlers.get(CHANNELS.maintenanceRelocateDataDirectory)!(event, authorization),
  ).rejects.toThrow('target_changed');
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('owned voice');
  expect(existsSync(join(target, 'voices'))).toBe(false);
  expect(await readDataDirectorySetting()).toBe(source);
  expect(await readFile(join(foreign, 'data', 'personal.txt'), 'utf8')).toBe('foreign data');
});

it('keeps the canonical destination and environment for an unchanged picker alias', async () => {
  const authorization = await choose();
  const result = await fixture.handlers.get(CHANNELS.maintenanceRelocateDataDirectory)!(
    event,
    authorization,
  );
  expect(result).toMatchObject({ path: target, removed_source: true });
  expect(existsSync(source)).toBe(false);
  expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('owned voice');
  expect(await readDataDirectorySetting()).toBe(target);
});

it('recognizes canonical backend startup writes when rolling back an alias-picked destination', async () => {
  await mkdir(join(source, 'outputs', 'longform_cache'), { recursive: true });
  const authorization = await choose();
  duringStart = async () => {
    await writeFile(
      join(target, 'outputs', 'longform_cache', 'voices_roots.json'),
      JSON.stringify([join(target, 'voices')]),
    );
    throw new Error('startup failed');
  };
  await expect(
    fixture.handlers.get(CHANNELS.maintenanceRelocateDataDirectory)!(event, authorization),
  ).rejects.toThrow('startup failed');
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('owned voice');
  expect(existsSync(join(target, 'voices'))).toBe(false);
  expect(existsSync(join(target, 'outputs'))).toBe(false);
  expect(await readDataDirectorySetting()).toBe(source);
});

it.each(['remote', 'unmanaged', 'untrusted'] as const)(
  'preserves %s IPC refusal before stopping or copying',
  async (reason) => {
    const authorization = await choose();
    if (reason === 'remote') fixture.remote = true;
    if (reason === 'unmanaged') fixture.managed = false;
    const caller = reason === 'untrusted' ? { sender: {}, senderFrame: event.senderFrame } : event;
    await expect(
      fixture.handlers.get(CHANNELS.maintenanceRelocateDataDirectory)!(caller, authorization),
    ).rejects.toThrow();
    expect(stop).not.toHaveBeenCalled();
    expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('owned voice');
    expect(await readDataDirectorySetting()).toBe(source);
  },
);
