// @vitest-environment node
import { constants } from 'node:fs';
import { chmod, lstat, mkdtemp, readFile, readlink, readdir, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const controls = vi.hoisted(() => ({
  handlers: new Map<string, (event: unknown, request: unknown) => Promise<unknown>>(),
  destination: '', failWrite: false, failRename: false, swapAfterProbeOpen: false, swapWithSymlink: false, appearWhileStaging: false,
}));
vi.mock('electron', () => ({
  app: {},
  BrowserWindow: { fromWebContents: () => owner },
  dialog: { showSaveDialog: async () => ({ canceled: false, filePath: controls.destination }) },
  ipcMain: { handle: (name: string, handler: (event: unknown, request: unknown) => Promise<unknown>) => controls.handlers.set(name, handler), on: () => {} },
  net: { fetch: async () => new Response(new Uint8Array([9, 8, 7, 6])) },
  shell: {}, systemPreferences: {},
}));
vi.mock('./uninstall-cleanup', () => ({ scheduleUninstallCleanup: () => {} }));
vi.mock('./site-browser', () => ({ registerSiteBrowser: () => {} }));
vi.mock('./pro-license', () => ({ activateProLicense: () => {}, deactivateProLicense: () => {}, proLicenseStatus: () => {} }));
vi.mock('node:fs/promises', async () => {
  const real = await vi.importActual<typeof import('node:fs/promises')>('node:fs/promises');
  return {
    ...real,
    writeFile: async (...args: Parameters<typeof real.writeFile>) => {
      if (controls.failWrite) {
        await real.writeFile(args[0], new Uint8Array([9]), args[2]);
        throw Object.assign(new Error('injected partial write failure'), { code: 'EIO' });
      }
      return real.writeFile(...args);
    },
    open: async (...args: Parameters<typeof real.open>) => {
      const file = await real.open(...args);
      if (controls.swapAfterProbeOpen && args[1] === constants.O_WRONLY && (args[0] === controls.destination || args[0] === await real.realpath(controls.destination))) {
        await real.rename(controls.destination, join(directory, 'authorized-original.wav'));
        if (controls.swapWithSymlink) {
          const unrelated = join(directory, 'unrelated.wav');
          await real.writeFile(unrelated, 'unrelated target', { mode: 0o444 });
          await real.chmod(unrelated, 0o444);
          await real.symlink('unrelated.wav', controls.destination);
        } else {
          await real.writeFile(controls.destination, 'unrelated replacement', { mode: 0o644 });
          await real.chmod(controls.destination, 0o644);
        }
      }
      const write = file.writeFile.bind(file);
      vi.spyOn(file, 'writeFile').mockImplementation(async (...data) => {
        if (controls.failWrite) {
          await write(new Uint8Array([9]));
          throw Object.assign(new Error('injected partial write failure'), { code: 'EIO' });
        }
        const result = await write(...data);
        if (controls.appearWhileStaging) {
          await real.writeFile(controls.destination, 'concurrent new export', { mode: 0o600 });
          controls.appearWhileStaging = false;
        }
        return result;
      });
      return file;
    },
    rename: async (...args: Parameters<typeof real.rename>) => {
      if (controls.failRename) throw Object.assign(new Error('injected rename failure'), { code: 'EACCES' });
      return real.rename(...args);
    },
  };
});
import { CHANNELS, registerIpc } from './ipc';

const frame = { url: 'app://voicestudio/index.html' };
const owner = { webContents: { mainFrame: frame } };
const event = { sender: owner.webContents, senderFrame: frame };
let directory: string;
beforeEach(async () => {
  controls.failWrite = false; controls.failRename = false; controls.swapAfterProbeOpen = false; controls.swapWithSymlink = false; controls.appearWhileStaging = false; controls.handlers.clear();
  directory = await mkdtemp(join(tmpdir(), 'voicestudio-export-'));
  controls.destination = join(directory, 'saved.wav');
  await writeFile(controls.destination, 'previous complete export', { mode: 0o600 });
  registerIpc({ baseUrl: 'http://127.0.0.1:8000', requestHeaders: () => ({}), subscribe: () => {} } as never, () => owner as never);
});
afterEach(async () => { controls.failWrite = false; controls.failRename = false; await rm(directory, { recursive: true, force: true }); vi.restoreAllMocks(); });
const requests = [
  [CHANNELS.filesSaveData, { suggestedName: 'saved.wav', data: new Uint8Array([9, 8, 7, 6]) }],
  [CHANNELS.filesSaveAudio, { suggestedName: 'saved.wav', url: 'http://127.0.0.1:8000/audio/source.wav' }],
] as const;

it.each(requests)('preserves the old export after a partial write in %s', async (channel, request) => {
  controls.failWrite = true;
  await expect(controls.handlers.get(channel)!(event, request)).rejects.toThrow('partial write');
  expect(await readFile(controls.destination, 'utf8')).toBe('previous complete export');
  expect(await readdir(directory)).toEqual(['saved.wav']);
});

it.each(requests)('preserves the old export after a failed replacement in %s', async (channel, request) => {
  controls.failRename = true;
  await expect(controls.handlers.get(channel)!(event, request)).rejects.toThrow('rename failure');
  expect(await readFile(controls.destination, 'utf8')).toBe('previous complete export');
  expect(await readdir(directory)).toEqual(['saved.wav']);
});

it.each(requests)('replaces the complete export and preserves its permissions in %s', async (channel, request) => {
  await expect(controls.handlers.get(channel)!(event, request)).resolves.toEqual({ canceled: false, path: controls.destination });
  expect([...await readFile(controls.destination)]).toEqual([9, 8, 7, 6]);
  if (process.platform !== 'win32') expect((await stat(controls.destination)).mode & 0o777).toBe(0o600);
  expect(await readdir(directory)).toEqual(['saved.wav']);
});

async function supportsSymlink(target: string, link: string, context: { skip(): void }): Promise<boolean> {
  try { await symlink(target, link); return true; }
  catch (error) {
    if (process.platform === 'win32' && ['EPERM', 'EACCES'].includes((error as NodeJS.ErrnoException).code || '')) {
      context.skip();
      return false;
    }
    throw error;
  }
}

it('preserves an existing symbolic link while replacing its target', async (context) => {
  const target = controls.destination;
  const link = join(directory, 'linked.wav');
  if (!await supportsSymlink('saved.wav', link, context)) return;
  controls.destination = link;
  await controls.handlers.get(CHANNELS.filesSaveData)!(event, requests[0][1]);
  expect([...await readFile(target)]).toEqual([9, 8, 7, 6]);
  expect([...await readFile(link)]).toEqual([9, 8, 7, 6]);
  expect(await readdir(directory)).toEqual(['linked.wav', 'saved.wav']);
});


it.each(requests)('creates a new export without leaving staging files in %s', async (channel, request) => {
  await rm(controls.destination);
  await controls.handlers.get(channel)!(event, request);
  expect([...await readFile(controls.destination)]).toEqual([9, 8, 7, 6]);
  expect(await readdir(directory)).toEqual(['saved.wav']);
});


it.each(requests)('preserves an existing mode despite a restrictive umask in %s', async (channel, request) => {
  if (process.platform === 'win32') return; // Windows does not implement POSIX mode bits.
  await chmod(controls.destination, 0o644);
  const previous = process.umask(0o077);
  try {
    await controls.handlers.get(channel)!(event, request);
    expect((await stat(controls.destination)).mode & 0o777).toBe(0o644);
  } finally { process.umask(previous); }
});

it('preserves a dangling symbolic link when its destination cannot be resolved', async (context) => {
  const link = join(directory, 'dangling.wav');
  if (!await supportsSymlink('missing.wav', link, context)) return;
  controls.destination = link;
  await expect(controls.handlers.get(CHANNELS.filesSaveData)!(event, requests[0][1])).rejects.toMatchObject({code: 'ENOENT'});
  expect((await lstat(link)).isSymbolicLink()).toBe(true);
  expect(await readlink(link)).toBe('missing.wav');
  expect(await readdir(directory)).toEqual(['dangling.wav', 'saved.wav']);
});


it.skipIf(process.platform === 'win32' || process.getuid?.() === 0).each(requests)('rejects a read-only existing export without replacing it in %s', async (channel, request) => {
  await chmod(controls.destination, 0o444);
  await expect(controls.handlers.get(channel)!(event, request)).rejects.toMatchObject({ code: expect.stringMatching(/^(EACCES|EPERM)$/) });
  expect(await readFile(controls.destination, 'utf8')).toBe('previous complete export');
  expect((await stat(controls.destination)).mode & 0o777).toBe(0o444);
  expect(await readdir(directory)).toEqual(['saved.wav']);
});

it('rejects a read-only symlink target without replacing the target or link', async (context) => {
  if (process.platform === 'win32' || process.getuid?.() === 0) {
    context.skip();
    return;
  }
  const target = controls.destination;
  const link = join(directory, 'linked.wav');
  await symlink('saved.wav', link);
  await chmod(target, 0o444);
  controls.destination = link;
  await expect(controls.handlers.get(CHANNELS.filesSaveData)!(event, requests[0][1])).rejects.toMatchObject({ code: expect.stringMatching(/^(EACCES|EPERM)$/) });
  expect(await readFile(target, 'utf8')).toBe('previous complete export');
  expect(await readlink(link)).toBe('saved.wav');
  expect(await readdir(directory)).toEqual(['linked.wav', 'saved.wav']);
});


it.skipIf(process.platform === 'win32' || process.getuid?.() === 0).each(requests)('leaves the existing export intact when the parent cannot stage a replacement in %s', async (channel, request) => {
  await chmod(directory, 0o500);
  try {
    // The existing inode remains writable even though its directory is not.
    await writeFile(controls.destination, 'previous complete export');
    await expect(controls.handlers.get(channel)!(event, request)).rejects.toMatchObject({ code: expect.stringMatching(/^(EACCES|EPERM)$/) });
    expect(await readFile(controls.destination, 'utf8')).toBe('previous complete export');
    expect(await readdir(directory)).toEqual(['saved.wav']);
  } finally {
    await chmod(directory, 0o700);
  }
});


it.skipIf(process.platform === 'win32').each(requests)('refuses a changed destination inode without overwriting either file in %s', async (channel, request) => {
  controls.swapAfterProbeOpen = true;
  await expect(controls.handlers.get(channel)!(event, request)).rejects.toMatchObject({ code: 'ESTALE' });
  expect((await stat(controls.destination)).mode & 0o777).toBe(0o644);
  expect(await readFile(controls.destination, 'utf8')).toBe('unrelated replacement');
  expect(await readFile(join(directory, 'authorized-original.wav'), 'utf8')).toBe('previous complete export');
  expect(await readdir(directory)).toEqual(['authorized-original.wav', 'saved.wav']);
});


it.skipIf(process.platform === 'win32').each(requests)('refuses a symlink substituted after authorization without overwriting its target in %s', async (channel, request) => {
  controls.swapAfterProbeOpen = true;
  controls.swapWithSymlink = true;
  await expect(controls.handlers.get(channel)!(event, request)).rejects.toMatchObject({ code: 'ESTALE' });
  expect(await readFile(join(directory, 'unrelated.wav'), 'utf8')).toBe('unrelated target');
  expect((await stat(join(directory, 'unrelated.wav'))).mode & 0o777).toBe(0o444);
  expect(await readFile(join(directory, 'authorized-original.wav'), 'utf8')).toBe('previous complete export');
  expect((await lstat(controls.destination)).isSymbolicLink()).toBe(true);
  expect(await readlink(controls.destination)).toBe('unrelated.wav');
  expect(await readdir(directory)).toEqual(['authorized-original.wav', 'saved.wav', 'unrelated.wav']);
});


it.each(requests)('preserves a destination that appears while a new export is staged in %s', async (channel, request) => {
  await rm(controls.destination);
  controls.appearWhileStaging = true;
  await expect(controls.handlers.get(channel)!(event, request)).rejects.toMatchObject({ code: 'EEXIST' });
  expect(await readFile(controls.destination, 'utf8')).toBe('concurrent new export');
  expect(await readdir(directory)).toEqual(['saved.wav']);
});
